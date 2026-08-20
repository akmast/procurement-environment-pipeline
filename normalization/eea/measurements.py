"""
EEA measurements normalization.

Reads every raw Parquet file under data/raw/eea/measurements/<year>/<pollutant>/
(one file at a time — ingestion.eea.measurements already scoped each file
to a single pollutant via a separate API request per pollutant) and
writes a normalized Parquet file with renamed, typed columns, mirroring
the same year/pollutant layout under data/normalized/eea/measurements/.

Pollutant identity comes from the raw file's own folder path — not from
a merge on the raw numeric `Pollutant` code. That raw code is kept too
(renamed to `pollutant_code`), in case it's useful later, but nothing in
this module merges on it.

Reads/writes go through common.storage, so storage_mode="local" (default)
and storage_mode="cloud" (S3) run the same logic.

    from normalization.eea.measurements import run
    run()
    run(storage_mode="cloud")
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

from common.storage import list_files, read_bytes, write_bytes

logger = logging.getLogger(__name__)

RAW_BASE_DIR = "data/raw/eea/measurements"
NORMALIZED_BASE_DIR = "data/normalized/eea/measurements"

# Explicit renames for columns confirmed via a live file (2026-08-19).
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
}


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


def add_pollutant_from_path(df: pd.DataFrame, raw_path: str) -> pd.DataFrame:
    """
    Pollutant name comes from the folder ingestion saved this file into
    (one API request per pollutant, see ingestion.eea.measurements) —
    reliable by construction, no merge on `pollutant_code` needed.
    """
    pollutant = raw_path.rsplit("/", 2)[-2]
    df.insert(0, "pollutant", pollutant)
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
    df = add_pollutant_from_path(df, raw_path)
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


def run(storage_mode: str = "local"):
    """
    Normalizes every raw measurements Parquet file found under
    data/raw/eea/measurements/<year>/<pollutant>/ — one output file per
    input file, same year/pollutant layout, under
    data/normalized/eea/measurements/.
    """
    raw_files = list_files(RAW_BASE_DIR, storage_mode, suffix=".parquet")
    if not raw_files:
        logger.warning("No raw measurements files found under %s", RAW_BASE_DIR)
        return

    logger.info("Starting EEA measurements normalization | files=%s storage_mode=%s",
                len(raw_files), storage_mode)
    for raw_path in raw_files:
        normalize_file(raw_path, storage_mode)
    logger.info("EEA measurements normalization finished | files=%s", len(raw_files))


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
    )
