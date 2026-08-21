"""
NUTS region boundaries ingestion — downloads the official NUTS3-level
boundary geometries (GeoJSON, WGS84 coordinates) from Eurostat's GISCO
distribution service, saved exactly as received. This is EU-wide
reference/lookup data, not scoped to any country and not a procurement or
air-quality fact — hence data/reference, not data/raw.

Used by transformation.eea.stations to derive nuts1_code/nuts2_code/
nuts3_code for each station from its (longitude, latitude), via
point-in-polygon matching. No semantic use of the geometries happens
here — this module only fetches and stores the raw file.

Source: https://gisco-services.ec.europa.eu/distribution/v2/nuts/
        (GISCO — Eurostat's geodata distribution service)

NOTE: the exact download URL below (year=2021, resolution=10M,
CRS=4326, LEVL_3) follows GISCO's documented file naming convention but
has not been live-verified in this sandbox — outbound access to
gisco-services.ec.europa.eu is blocked here. Confirm on first real run;
if it 404s, check available files/years at the distribution page above.

Reads/writes go through common.storage, so storage_mode="local" (default)
and storage_mode="cloud" (S3) run the exact same logic. The file is
staged, validated as well-formed GeoJSON, and only then hash-compared
against what's already stored (see common/staged_write.py) — written to
final storage only if both checks pass. NUTS boundaries change rarely
(new NUTS classification versions come out every few years), so most
runs should find nothing changed.

    from ingestion.eea.nuts_boundaries import run
    run()
    run(storage_mode="cloud")

Requires: pip install requests
"""
import logging
import sys
from pathlib import Path

import requests

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.logging_config import setup_logging
from common.change_tracking import load_state, save_state
from common.manifest import StageResult
from common.staged_write import WRITE_RESULT_WRITTEN, stage_validate_and_write
from common.validation import is_valid_geojson

setup_logging()
logger = logging.getLogger(__name__)

OUT_DIR = "data/reference/eea/nuts_boundaries"
STATE_PATH = f"{OUT_DIR}/state.json"
OUT_PATH = f"{OUT_DIR}/nuts3_boundaries.geojson"

# LEVL_3 = NUTS3 boundaries only — NUTS1/NUTS2 codes are derived from the
# NUTS3 code by prefix (see transformation.eea.stations), so we don't need
# separate boundary files for those levels. 4326 = WGS84 lat/lon, matching
# the coordinate system EEA station coordinates already come in (outSR=4326
# in ingestion.eea.stations) — no reprojection needed.
NUTS_BOUNDARIES_URL = (
    "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"
    "NUTS_RG_10M_2021_4326_LEVL_3.geojson"
)


def fetch_nuts_boundaries() -> bytes | None:
    resp = requests.get(NUTS_BOUNDARIES_URL, timeout=60)
    logger.info("NUTS boundaries requested | status=%s size_bytes=%s",
                resp.status_code, len(resp.content))

    if not resp.ok:
        logger.error(
            "NUTS boundaries download failed | status=%s — check the exact "
            "filename/year at https://gisco-services.ec.europa.eu/distribution/v2/nuts/",
            resp.status_code,
        )
        return None

    return resp.content


def run(storage_mode: str = "local") -> StageResult:
    """
    Entry point to be imported and called from main.py, e.g.:

        from ingestion.eea.nuts_boundaries import run
        result = run()
    """
    logger.info("Starting NUTS boundaries ingestion | storage_mode=%s", storage_mode)
    state = load_state(STATE_PATH, storage_mode)
    result = StageResult()

    geojson_bytes = fetch_nuts_boundaries()
    if geojson_bytes is None:
        logger.warning("NUTS boundaries ingestion finished | no file written — download failed")
        result.record_failed(OUT_PATH)
        return result.finalize(attempted=1)

    write_result = stage_validate_and_write(
        OUT_PATH, geojson_bytes, storage_mode, state, validate=is_valid_geojson
    )
    save_state(STATE_PATH, state, storage_mode)

    if write_result == WRITE_RESULT_WRITTEN:
        result.record_written(OUT_PATH)
        logger.info("NUTS boundaries saved | size_bytes=%s path=%s", len(geojson_bytes), OUT_PATH)
    elif write_result == "unchanged":
        result.record_unchanged(OUT_PATH)
        logger.info("NUTS boundaries not written (unchanged) | path=%s", OUT_PATH)
    else:
        result.record_failed(OUT_PATH)
        logger.error("NUTS boundaries not written (invalid) | path=%s", OUT_PATH)

    return result.finalize(attempted=1)


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
    )
