"""
EEA station metadata normalization.

Reads the raw ArcGIS feature list saved by ingestion.eea.stations and turns
it into a flat, readable table: extracts attributes + geometry into
columns, drops the non-analytical PopupInfo blob. No deduplication here —
that's transformation.eea.stations. No country filtering either —
ingestion already scoped the request to COUNTRY server-side.

Reads/writes go through common.storage, so storage_mode="local" (default)
and storage_mode="cloud" (S3) run the same logic.

    from normalization.eea.stations import run
    run()
    run(storage_mode="cloud")
"""
import json
import logging
import sys
from io import BytesIO
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.storage import read_bytes, write_bytes

logger = logging.getLogger(__name__)

RAW_PATH = "data/raw/eea/stations/stations_raw.json"
STATION_METADATA_PATH = "data/normalized/eea/stations/station_metadata.parquet"


def load_raw_features(storage_mode: str) -> list[dict]:
    return json.loads(read_bytes(RAW_PATH, storage_mode).decode("utf-8"))


def flatten_features(features: list[dict]) -> pd.DataFrame:
    """Each raw feature is {attributes: {...}, geometry: {x, y}} — flatten to one row."""
    rows = []
    for feature in features:
        row = dict(feature.get("attributes", {}))
        geom = feature.get("geometry") or {}
        row["longitude"] = geom.get("x")
        row["latitude"] = geom.get("y")
        rows.append(row)
    return pd.DataFrame(rows)


def drop_popup_info(df: pd.DataFrame) -> pd.DataFrame:
    """
    PopupInfo is decorative HTML for the EEA web map popup (station
    description + per-pollutant download links) — not analytical data,
    and it's large enough to flood storage. Confirmed via live response
    on 2026-08-18.
    """
    if "PopupInfo" in df.columns:
        df = df.drop(columns=["PopupInfo"])
    return df


def run(storage_mode: str = "local"):
    logger.info("Starting EEA station metadata normalization | storage_mode=%s", storage_mode)
    features = load_raw_features(storage_mode)
    df = flatten_features(features)
    df = drop_popup_info(df)

    buffer = BytesIO()
    df.to_parquet(buffer, index=False)
    write_bytes(STATION_METADATA_PATH, buffer.getvalue(), storage_mode)

    logger.info("Normalized stations saved | path=%s rows=%s",
                STATION_METADATA_PATH, len(df))
    logger.info("Columns | %s", list(df.columns))
    if not df.empty:
        logger.info("Sample rows:\n%s", df.head(3).to_string())


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
    )
