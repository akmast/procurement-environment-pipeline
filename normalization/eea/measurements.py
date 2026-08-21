"""
EEA measurements normalization.

Reads every raw Parquet file under
data/raw/eea/measurements/<country>/<year>/<pollutant>/ (one file at a
time — ingestion.eea.measurements already scoped each file to a single
country and pollutant via a separate API request per country/pollutant
pair) and writes a normalized Parquet file with renamed, typed columns,
mirroring the same country/year/pollutant layout under
data/normalized/eea/measurements/.

Pollutant identity comes from the raw file's own folder path — not from
a merge on the raw numeric `Pollutant` code. That raw code is kept too
(renamed to `pollutant_code`), in case it's useful later, but nothing in
this module merges on it. Country identity (`country_code`) is added the
same way: the raw Parquet schema has no country field at all, so it's
read from the file's own folder path — which ingestion already laid out
per country — rather than guessed from any in-file content.

`countries` must be passed explicitly — run() never defaults to scanning
and processing every country on disk. Pass discover_countries(storage_mode)
yourself to process everything currently ingested (it just lists the raw
layer's own top-level <country>/ subdirectories) — that way "process
everything" is always a deliberate choice at the call site.

Each entry can be a partition prefix at any granularity under
data/raw/eea/measurements/ — "DE" (a whole country), "DE/2025" (one
year), "DE/2025/PM10" (one country/year/pollutant) — or an exact
*.parquet file path (see common.storage.resolve_paths). The exact-path
form is what makes "only the files a refresh run just wrote" possible:
pass ingestion.eea.measurements' own returned written_paths straight
into normalization/transformation instead of re-scanning (and
re-normalizing) the whole country.

Reads/writes go through common.storage, so storage_mode="local" (default)
and storage_mode="cloud" (S3) run the same logic.

    from normalization.eea.measurements import run, discover_countries
    run(countries=["DE", "PL"])
    run(countries=["DE/2025/PM10"])
    run(countries=discover_countries("local"))
    run(countries=["DE"], storage_mode="cloud")
"""
import logging
import re
import sys
from io import BytesIO
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.manifest import StageResult
from common.storage import list_files, read_bytes, resolve_paths, write_bytes

logger = logging.getLogger(__name__)

RAW_BASE_DIR = "data/raw/eea/measurements"
NORMALIZED_BASE_DIR = "data/normalized/eea/measurements"

# Explicit renames for columns confirmed via live files (2026-08-19).
# Anything not listed here still gets converted (see _to_snake_case) and
# logged as a warning, instead of being silently dropped or guessed at.
COLUMN_RENAME = {
    "Samplingpoint": "sampling_point",
    "Pollutant": "pollutant_code",  # raw numeric EEA vocabulary code — not the same as `pollutant` below
    "Start": "period_start",
    "End": "period_end",
    "Value": "value",
    "Unit": "unit",
    "AggType": "aggregation_type",
    "Validity": "validity",
    "Verification": "verification",
    "ResultTime": "result_time",
    "DataCapture": "data_capture",
    "FkObservationLog": "fk_observation_log",  # confirmed live 2026-08-19 — was a guessed name before
}

# Dropped after renaming — confirmed via live files (2026-08-19):
#   data_capture      always null across every observed file, no information to carry
#   fk_observation_log  internal EEA housekeeping reference, not useful at this stage
DROP_COLUMNS = ["data_capture", "fk_observation_log"]


def _to_snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def rename_columns(df: pd.DataFrame) -> pd.DataFrame:
    unmapped = [c for c in df.columns if c not in COLUMN_RENAME]
    if unmapped:
        logger.warning(
            "Column(s) not in COLUMN_RENAME, auto-converted to snake_case instead "
            "of an explicit name | columns=%s", unmapped,
        )
    rename = {c: _to_snake_case(c) for c in unmapped}
    rename.update(COLUMN_RENAME)
    return df.rename(columns=rename)


def drop_unused_columns(df: pd.DataFrame) -> pd.DataFrame:
    present = [c for c in DROP_COLUMNS if c in df.columns]
    if present:
        df = df.drop(columns=present)
    return df


def add_pollutant_from_path(df: pd.DataFrame, raw_path: str) -> pd.DataFrame:
    """
    Pollutant name comes from the folder ingestion saved this file into
    (one API request per pollutant, see ingestion.eea.measurements) —
    reliable by construction, no merge on `pollutant_code` needed.
    """
    pollutant = raw_path.rsplit("/", 2)[-2]
    df.insert(0, "pollutant", pollutant)
    return df


def add_country_from_path(df: pd.DataFrame, raw_path: str) -> pd.DataFrame:
    """
    Country code comes from the file's own folder path
    (data/raw/eea/measurements/<country>/<year>/<pollutant>/*.parquet) —
    known from the per-country API request that produced it (see
    ingestion.eea.measurements), never guessed from in-file content.
    """
    relative = raw_path[len(RAW_BASE_DIR):].lstrip("/")
    country = relative.split("/")[0]
    df.insert(0, "country_code", country)
    return df


def cast_types(df: pd.DataFrame) -> pd.DataFrame:
    # Value comes back as a Decimal-backed column (parquet decimal logical
    # type) — cast to a plain float for normal arithmetic/aggregation.
    df["value"] = pd.to_numeric(df["value"], errors="coerce").astype(float)
    df["period_start"] = pd.to_datetime(df["period_start"], errors="coerce")
    df["period_end"] = pd.to_datetime(df["period_end"], errors="coerce")
    df["result_time"] = pd.to_datetime(df["result_time"], errors="coerce")
    df["pollutant_code"] = pd.to_numeric(df["pollutant_code"], errors="coerce").astype("Int64")
    df["validity"] = pd.to_numeric(df["validity"], errors="coerce").astype("Int64")
    df["verification"] = pd.to_numeric(df["verification"], errors="coerce").astype("Int64")
    return df


def normalize_file(raw_path: str, storage_mode: str) -> str:
    df = pd.read_parquet(BytesIO(read_bytes(raw_path, storage_mode)))

    df = rename_columns(df)
    df = drop_unused_columns(df)
    df = add_pollutant_from_path(df, raw_path)
    df = add_country_from_path(df, raw_path)
    df = cast_types(df)

    relative = raw_path[len(RAW_BASE_DIR):].lstrip("/")
    out_path = f"{NORMALIZED_BASE_DIR}/{relative}"

    buffer = BytesIO()
    df.to_parquet(buffer, index=False)
    write_bytes(out_path, buffer.getvalue(), storage_mode)

    logger.info(
        "Normalized file saved | raw=%s -> normalized=%s rows=%s",
        raw_path, out_path, len(df),
    )
    return out_path


def discover_countries(storage_mode: str) -> list[str]:
    """Country codes come from the raw layer's own <country>/ subdirectories."""
    raw_files = list_files(RAW_BASE_DIR, storage_mode, suffix=".parquet")
    return sorted({path[len(RAW_BASE_DIR):].lstrip("/").split("/")[0] for path in raw_files})


def run(storage_mode: str = "local", countries: list[str] | None = None) -> StageResult:
    """
    Normalizes every raw measurements Parquet file found under each of
    `countries` (relative paths under data/raw/eea/measurements/, e.g.
    "DE" or "DE/2025/PM10") — one output file per input file, same
    layout, under data/normalized/eea/measurements/. `countries` is
    required; pass discover_countries(storage_mode) to process
    everything currently ingested. A failed file is isolated (logged +
    recorded in failed_paths) and doesn't stop the rest of the batch. An
    empty raw_files match is not an error — returns a SKIPPED StageResult.
    """
    if not countries:
        raise ValueError(
            "countries must be provided explicitly — e.g. countries=['DE'], or "
            "countries=discover_countries(storage_mode) to process every country "
            "already ingested. run() does not default to processing everything on disk."
        )

    raw_files = resolve_paths(countries, RAW_BASE_DIR, storage_mode, suffix=".parquet")

    if not raw_files:
        logger.warning("No raw measurements files found for countries=%s under %s", countries, RAW_BASE_DIR)
        return StageResult().finalize(attempted=0)

    logger.info("Starting EEA measurements normalization | countries=%s files=%s storage_mode=%s",
                countries, len(raw_files), storage_mode)

    result = StageResult()
    for raw_path in raw_files:
        try:
            out_path = normalize_file(raw_path, storage_mode)
            result.record_written(out_path)
        except Exception:
            logger.exception("Measurements normalization failed | raw=%s", raw_path)
            result.record_failed(raw_path)

    logger.info("EEA measurements normalization finished | files=%s written=%s failed=%s",
                len(raw_files), len(result.written_paths), len(result.failed_paths))
    return result.finalize(attempted=len(raw_files))


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
        countries=["DE"],      # required — e.g. ["DE", "PL"], or discover_countries("local") for everything
    )
