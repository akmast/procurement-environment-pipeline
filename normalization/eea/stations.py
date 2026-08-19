"""
EEA station metadata normalization.

Reads the raw ArcGIS feature list saved by ingestion.eea.stations and turns
it into a flat, readable table: extracts attributes + geometry into
columns, drops the non-analytical PopupInfo blob. No deduplication here —
that's transformation.eea.stations. No country filtering either —
ingestion already scoped the request to COUNTRY server-side.

    from normalization.eea.stations import run
    run()
"""
import json
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

RAW_PATH = Path("data/raw/eea/stations/stations_raw.json")
OUT_DIR = Path("data/normalized/eea/stations")
OUT_DIR.mkdir(parents=True, exist_ok=True)
STATION_METADATA_PATH = OUT_DIR / "station_metadata.parquet"


def load_raw_features() -> list[dict]:
    if not RAW_PATH.exists():
        raise FileNotFoundError(
            f"No raw stations file at {RAW_PATH} — run ingestion.eea.stations first."
        )
    return json.loads(RAW_PATH.read_text())


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


def run():
    logger.info("Starting EEA station metadata normalization")
    features = load_raw_features()
    df = flatten_features(features)
    df = drop_popup_info(df)

    df.to_parquet(STATION_METADATA_PATH, index=False)
    logger.info("Normalized stations saved | path=%s rows=%s",
                STATION_METADATA_PATH.resolve(), len(df))
    logger.info("Columns | %s", list(df.columns))
    if not df.empty:
        logger.info("Sample rows:\n%s", df.head(3).to_string())


if __name__ == "__main__":
    run()
