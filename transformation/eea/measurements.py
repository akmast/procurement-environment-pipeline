"""
EEA measurements transformation — column selection + station join.

Reads every normalized measurements Parquet file (one per raw file, see
normalization.eea.measurements), keeps only the columns that matter for
analysis, and joins in station data — location and NUTS1/NUTS2/NUTS3 —
from the already-enriched transformation.eea.stations output for the
*same country* as the measurement file. NUTS codes are never recomputed
here: they're a station-level property, already derived once from
station coordinates in transformation.eea.stations.

Each normalized measurements file lives under
data/normalized/eea/measurements/<country>/<year>/<pollutant>/ — the
country is read directly from that path (known from ingestion's own
per-country request, not guessed) and used to pick the matching
data/transformed/eea/stations/<country>/station_metadata.parquet lookup
table. Each country's station table is loaded once and reused for every
one of its measurement files.

Join key: a station's EoI code, e.g. "DEBE034". Measurements don't carry
this as its own column — it's embedded inside `sampling_point`, e.g.
"SPO.DE_DEBE034_PM1_dataGroup2", whose second underscore-separated
segment is the code — extracted explicitly by extract_station_code() and
matched against `AirQualityStationEoICode` in the transformed stations
table. A measurement whose station can't be matched keeps its row (left
join) with location/nuts1_code/nuts2_code/nuts3_code left empty, rather
than being dropped or crashing the run.

`countries` must be passed explicitly — run() never defaults to scanning
and processing every country on disk. Pass discover_countries(storage_mode)
yourself to process everything currently normalized — that way "process
everything" is always a deliberate choice at the call site.

Each entry can be a partition prefix at any granularity under
data/normalized/eea/measurements/ ("DE", "DE/2025", "DE/2025/PM10") or
an exact *.parquet file path (see common.storage.resolve_paths) — same
convention as normalization.eea.measurements, so the exact set of files
normalization just (re)wrote on a refresh run can be passed straight
through to transformation without re-touching the rest of the country.

Reads/writes go through common.storage, so storage_mode="local" (default)
and storage_mode="cloud" (S3) run the same logic.

    from transformation.eea.measurements import run, discover_countries
    run(countries=["DE", "PL"])
    run(countries=discover_countries("local"))
    run(countries=["DE"], storage_mode="cloud")
"""
import logging
import sys
from io import BytesIO
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.storage import exists, list_files, read_bytes, resolve_paths, write_bytes

logger = logging.getLogger(__name__)

NORMALIZED_BASE_DIR = "data/normalized/eea/measurements"
TRANSFORMED_BASE_DIR = "data/transformed/eea/measurements"
STATIONS_BASE_DIR = "data/transformed/eea/stations"
STATION_METADATA_FILENAME = "station_metadata.parquet"

# Names as produced by normalization.eea.measurements. `country_code` is
# the pipeline's own request-level country, kept as-is. `pollutant` (the
# human-readable name added from the folder path) is kept, not
# `pollutant_code` (the raw numeric EEA code) — that's the identifier
# actually useful for analysis.
KEEP_COLUMNS = [
    "country_code", "sampling_point", "pollutant", "period_start", "period_end",
    "value", "unit", "aggregation_type", "validity", "verification", "result_time",
]

# Columns pulled in from the already-enriched transformed stations table.
# AQStationName is exposed here as "location" — the plain station name.
STATION_COLUMNS = ["AirQualityStationEoICode", "AQStationName", "nuts1_code", "nuts2_code", "nuts3_code"]
STATION_RENAME = {"AQStationName": "location"}


def load_stations(country: str, storage_mode: str) -> pd.DataFrame:
    path = f"{STATIONS_BASE_DIR}/{country}/{STATION_METADATA_FILENAME}"
    if not exists(path, storage_mode):
        raise FileNotFoundError(
            f"No transformed stations file at {path} — "
            f"run transformation.eea.stations for country={country} first."
        )
    df = pd.read_parquet(BytesIO(read_bytes(path, storage_mode)))
    return df[STATION_COLUMNS].rename(columns=STATION_RENAME)


def extract_country_from_path(normalized_path: str) -> str:
    """Country is the first path segment under NORMALIZED_BASE_DIR — known
    from the normalized layer's own directory structure, not guessed."""
    relative = normalized_path[len(NORMALIZED_BASE_DIR):].lstrip("/")
    return relative.split("/")[0]


def extract_station_code(sampling_point) -> str | None:
    """
    sampling_point looks like "SPO.DE_DEBE034_PM1_dataGroup2" — the
    second underscore-separated segment ("DEBE034") is the station's EoI
    code, matching AirQualityStationEoICode in the stations dataset
    (confirmed against real EEA sampling point values, 2026-08-19).
    """
    if not isinstance(sampling_point, str):
        return None
    parts = sampling_point.split("_")
    if len(parts) < 2:
        return None
    return parts[1]


def select_columns(df: pd.DataFrame) -> pd.DataFrame:
    present = [c for c in KEEP_COLUMNS if c in df.columns]
    missing = [c for c in KEEP_COLUMNS if c not in df.columns]
    if missing:
        logger.warning("Expected column(s) missing from normalized measurements | columns=%s", missing)
    return df[present].copy()


def join_station_data(df: pd.DataFrame, stations: pd.DataFrame) -> pd.DataFrame:
    before = len(df)
    df["station_code"] = df["sampling_point"].apply(extract_station_code)

    merged = df.merge(stations, how="left", left_on="station_code", right_on="AirQualityStationEoICode")
    merged = merged.drop(columns=["station_code", "AirQualityStationEoICode"])

    unmatched = merged["location"].isna().sum()
    if unmatched:
        logger.warning("Measurements without a matching station | unmatched=%s/%s", unmatched, before)
    return merged


def transform_file(normalized_path: str, stations: pd.DataFrame, storage_mode: str) -> str:
    df = pd.read_parquet(BytesIO(read_bytes(normalized_path, storage_mode)))
    df = select_columns(df)
    df = join_station_data(df, stations)

    relative = normalized_path[len(NORMALIZED_BASE_DIR):].lstrip("/")
    out_path = f"{TRANSFORMED_BASE_DIR}/{relative}"

    buffer = BytesIO()
    df.to_parquet(buffer, index=False)
    write_bytes(out_path, buffer.getvalue(), storage_mode)

    logger.info(
        "Transformed measurements file saved | normalized=%s -> transformed=%s rows=%s",
        normalized_path, out_path, len(df),
    )
    return out_path


def discover_countries(storage_mode: str) -> list[str]:
    """Country codes come from the normalized layer's own <country>/ subdirectories."""
    normalized_files = list_files(NORMALIZED_BASE_DIR, storage_mode, suffix=".parquet")
    return sorted({extract_country_from_path(path) for path in normalized_files})


def run(storage_mode: str = "local", countries: list[str] | None = None):
    """
    Transforms every normalized measurements Parquet file found under
    each of `countries` — one output file per input file, same layout,
    under data/transformed/eea/measurements/. `countries` is required;
    pass discover_countries(storage_mode) to process everything
    currently normalized.
    """
    if not countries:
        raise ValueError(
            "countries must be provided explicitly — e.g. countries=['DE'], or "
            "countries=discover_countries(storage_mode) to process every country "
            "already normalized. run() does not default to processing everything on disk."
        )

    logger.info("Starting EEA measurements transformation | countries=%s storage_mode=%s",
                countries, storage_mode)

    normalized_files = resolve_paths(countries, NORMALIZED_BASE_DIR, storage_mode, suffix=".parquet")

    if not normalized_files:
        logger.warning("No normalized measurements files found for countries=%s under %s",
                       countries, NORMALIZED_BASE_DIR)
        return

    stations_cache: dict[str, pd.DataFrame] = {}
    for normalized_path in normalized_files:
        country = extract_country_from_path(normalized_path)
        if country not in stations_cache:
            stations_cache[country] = load_stations(country, storage_mode)
        transform_file(normalized_path, stations_cache[country], storage_mode)

    logger.info("EEA measurements transformation finished | files=%s", len(normalized_files))


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
        countries=["DE"],      # required — e.g. ["DE", "PL"], or discover_countries("local") for everything
    )
