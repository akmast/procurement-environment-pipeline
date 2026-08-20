"""
EEA station metadata normalization.

Reads the raw ArcGIS feature list saved by ingestion.eea.stations — one
file per country, data/raw/eea/stations/<country>/stations_raw.json — and
turns each into a flat, readable table: extracts attributes + geometry
into columns, drops the non-analytical PopupInfo blob. No deduplication
here — that's transformation.eea.stations. No country filtering either —
ingestion already scoped each file's request to one country server-side.

The raw ArcGIS attributes already include a per-row `CountryCode` field
(confirmed live, see ingestion.eea.stations) — that's this dataset's
country column, carried through unchanged; no separate country_code is
added on top of it.

If `countries` isn't passed, every country already ingested (i.e. every
subdirectory under data/raw/eea/stations/) is processed — read from the
raw layer's own directory structure, not guessed from file content.

Reads/writes go through common.storage, so storage_mode="local" (default)
and storage_mode="cloud" (S3) run the same logic.

    from normalization.eea.stations import run
    run()
    run(countries=["DE", "PL"])
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

from common.storage import list_files, read_bytes, write_bytes

logger = logging.getLogger(__name__)

RAW_BASE_DIR = "data/raw/eea/stations"
NORMALIZED_BASE_DIR = "data/normalized/eea/stations"
RAW_FILENAME = "stations_raw.json"


def discover_countries(storage_mode: str) -> list[str]:
    """Country codes come from the raw layer's own <country>/ subdirectories."""
    raw_files = list_files(RAW_BASE_DIR, storage_mode, suffix=RAW_FILENAME)
    return sorted({Path(p).parent.name for p in raw_files})


def load_raw_features(country: str, storage_mode: str) -> list[dict]:
    path = f"{RAW_BASE_DIR}/{country}/{RAW_FILENAME}"
    return json.loads(read_bytes(path, storage_mode).decode("utf-8"))


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


def run(storage_mode: str = "local", countries: list[str] | None = None):
    countries = countries or discover_countries(storage_mode)
    if not countries:
        logger.warning("No raw station files found under %s", RAW_BASE_DIR)
        return

    logger.info("Starting EEA station metadata normalization | countries=%s storage_mode=%s",
                countries, storage_mode)

    for country in countries:
        features = load_raw_features(country, storage_mode)
        df = flatten_features(features)
        df = drop_popup_info(df)

        out_path = f"{NORMALIZED_BASE_DIR}/{country}/station_metadata.parquet"
        buffer = BytesIO()
        df.to_parquet(buffer, index=False)
        write_bytes(out_path, buffer.getvalue(), storage_mode)

        logger.info("Normalized stations saved | country=%s path=%s rows=%s",
                    country, out_path, len(df))
        logger.info("Columns | %s", list(df.columns))
        if not df.empty:
            logger.info("Sample rows:\n%s", df.head(3).to_string())


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
        countries=["DE"],      # e.g. ["DE", "PL"] — omit/None to auto-discover from data/raw/eea/stations/
    )
