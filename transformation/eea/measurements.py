"""
EEA measurements transformation — column selection + station join.

Reads every normalized measurements Parquet file (one per raw file, see
normalization.eea.measurements), keeps only the columns that matter for
analysis, and joins in station data — location and NUTS1/NUTS2/NUTS3 —
from the already-enriched transformation.eea.stations output. NUTS codes
are never recomputed here: they're a station-level property, already
derived once from station coordinates in transformation.eea.stations.

Join key: a station's EoI code, e.g. "DEBE034". Measurements don't carry
this as its own column — it's embedded inside `sampling_point`, e.g.
"SPO.DE_DEBE034_PM1_dataGroup2", whose second underscore-separated
segment is the code — extracted explicitly by extract_station_code() and
matched against `AirQualityStationEoICode` in the transformed stations
table. A measurement whose station can't be matched keeps its row (left
join) with location/nuts1_code/nuts2_code/nuts3_code left empty, rather
than being dropped or crashing the run.

Reads/writes go through common.storage, so storage_mode="local" (default)
and storage_mode="cloud" (S3) run the same logic.

    from transformation.eea.measurements import run
    run()
    run(storage_mode="cloud")
"""
import logging
import sys
from io import BytesIO
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.storage import exists, list_files, read_bytes, write_bytes

logger = logging.getLogger(__name__)

NORMALIZED_BASE_DIR = "data/normalized/eea/measurements"
TRANSFORMED_BASE_DIR = "data/transformed/eea/measurements"
STATIONS_PATH = "data/transformed/eea/stations/station_metadata.parquet"

# Names as produced by normalization.eea.measurements. `pollutant` (the
# human-readable name added from the folder path) is kept, not
# `pollutant_code` (the raw numeric EEA code) — that's the identifier
# actually useful for analysis.
KEEP_COLUMNS = [
    "sampling_point", "pollutant", "period_start", "period_end", "value",
    "unit", "aggregation_type", "validity", "verification", "result_time",
]

# Columns pulled in from the already-enriched transformed stations table.
# AQStationName is exposed here as "location" — the plain station name.
STATION_COLUMNS = ["AirQualityStationEoICode", "AQStationName", "nuts1_code", "nuts2_code", "nuts3_code"]
STATION_RENAME = {"AQStationName": "location"}


def load_stations(storage_mode: str) -> pd.DataFrame:
    if not exists(STATIONS_PATH, storage_mode):
        raise FileNotFoundError(
            f"No transformed stations file at {STATIONS_PATH} — "
            f"run transformation.eea.stations first."
        )
    df = pd.read_parquet(BytesIO(read_bytes(STATIONS_PATH, storage_mode)))
    return df[STATION_COLUMNS].rename(columns=STATION_RENAME)


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


def run(storage_mode: str = "local"):
    """
    Transforms every normalized measurements Parquet file found under
    data/normalized/eea/measurements/<year>/<pollutant>/ — one output
    file per input file, same year/pollutant layout, under
    data/transformed/eea/measurements/.
    """
    logger.info("Starting EEA measurements transformation | storage_mode=%s", storage_mode)
    stations = load_stations(storage_mode)

    normalized_files = list_files(NORMALIZED_BASE_DIR, storage_mode, suffix=".parquet")
    if not normalized_files:
        logger.warning("No normalized measurements files found under %s", NORMALIZED_BASE_DIR)
        return

    for normalized_path in normalized_files:
        transform_file(normalized_path, stations, storage_mode)

    logger.info("EEA measurements transformation finished | files=%s", len(normalized_files))


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
    )
