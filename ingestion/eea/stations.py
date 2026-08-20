"""
EEA station metadata ingestion — standalone, no join with measurements.

Saves the raw ArcGIS feature list exactly as received (each feature keeps
its separate `attributes` + `geometry` shape). Country scope is applied
server-side via the ArcGIS `where` filter — no other filtering, no
flattening, no dropped columns, no dedup — that belongs to normalization.

Supports multiple countries: one ArcGIS query per country (mirrors the
one-request-per-pollutant pattern in ingestion.eea.measurements), each
written to its own path — data/raw/eea/stations/<country>/stations_raw.json
— with its own state.json, so a failed/changed fetch for one country
never affects another's staging/hash state.

Reads/writes go through common.storage, so storage_mode="local" (default,
used for development/testing and by the __main__ example below) and
storage_mode="cloud" (S3, via PIPELINE_S3_BUCKET) run the exact same
logic — see common/storage.py. The raw JSON is staged, validated, and
hash-compared before it's allowed to reach final storage — see
common/staged_write.py and docs/storage_and_incremental.md.

    from ingestion.eea.stations import run
    run(mode="stations")
    run(mode="stations", countries=["DE", "PL"])
    run(mode="stations", storage_mode="cloud")
"""
import json
import logging
import sys
from pathlib import Path

# Make `common`/`ingestion` importable and logging configured regardless of
# how this file is run (python -m, import, Jupyter, or run directly —
# a direct run has no package context, so the project root isn't on
# sys.path yet, same reason .http_client needs the try/except below).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.logging_config import setup_logging
from common.change_tracking import load_state, save_state
from common.staged_write import stage_validate_and_write
from common.validation import is_valid_json

try:
    from .http_client import request_with_retry
except ImportError:
    from http_client import request_with_retry

setup_logging()
logger = logging.getLogger(__name__)

# Confirmed via live response (2026-08-14): fieldAliases = OBJECTID,
# AirQualityStation, Country, CountryCode, AirQualityStationEoICode,
# AQStationName, stationClass, PopupInfo. Geometry (lat/lon) comes
# separately per feature, requested here in WGS84 via outSR=4326.
STATIONS_ENDPOINT = (
    "https://air.discomap.eea.europa.eu/arcgis/rest/services/AirQuality/"
    "AirQualityDownloadServiceEUMonitoringStations/MapServer/0/query"
)
STATIONS_PAGE_SIZE = 2000
DEFAULT_COUNTRIES = ["DE"]  # preserves the pipeline's previous single-country behavior

OUT_DIR = "data/raw/eea/stations"


def fetch_raw_features(country: str) -> list[dict]:
    """
    Page through the ArcGIS query endpoint, filtered server-side to
    `country` via the `where` clause (standard ArcGIS REST attribute
    filtering — not a post-fetch filter), and return the raw feature
    list, untouched.
    """
    all_features = []
    offset = 0
    while True:
        params = {
            "where": f"CountryCode='{country}'",
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": STATIONS_PAGE_SIZE,
        }
        resp = request_with_retry("GET", STATIONS_ENDPOINT, params=params)
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Stations query failed | country={country}: {data['error']}")

        features = data.get("features", [])
        all_features.extend(features)
        logger.info("Station page fetched | country=%s offset=%s count=%s", country, offset, len(features))

        if not data.get("exceededTransferLimit") and len(features) < STATIONS_PAGE_SIZE:
            break
        if not features:
            break
        offset += len(features)

    if not all_features:
        raise RuntimeError(f"Stations query returned 0 features | country={country}")

    return all_features


def run_stations(countries: list[str], storage_mode: str):
    logger.info("Starting EEA station metadata ingestion | mode=stations countries=%s storage_mode=%s",
                countries, storage_mode)

    for country in countries:
        features = fetch_raw_features(country)
        content = json.dumps(features, ensure_ascii=False).encode("utf-8")

        raw_path = f"{OUT_DIR}/{country}/stations_raw.json"
        state_path = f"{OUT_DIR}/{country}/state.json"
        state = load_state(state_path, storage_mode)

        written = stage_validate_and_write(
            raw_path, content, storage_mode, state, validate=is_valid_json
        )

        if not written:
            logger.info("No update written | country=%s features=%s", country, len(features))
            continue

        save_state(state_path, state, storage_mode)
        logger.info("Raw stations saved | country=%s path=%s features=%s storage_mode=%s",
                    country, raw_path, len(features), storage_mode)


def run(mode: str, storage_mode: str = "local", countries: list[str] | None = None, **kwargs):
    countries = countries or DEFAULT_COUNTRIES
    if mode == "stations":
        run_stations(countries, storage_mode)
    else:
        raise ValueError(f"Unknown mode {mode!r} — expected 'stations'")


if __name__ == "__main__":
    run(
        mode="stations",             # Only supported mode — fetches the full raw station list
        storage_mode="local",        # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
        countries=["DE"],            # e.g. ["DE", "PL"] — one ArcGIS query per country
    )
