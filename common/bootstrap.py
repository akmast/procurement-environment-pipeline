"""
Bootstrap completion manifest.

Reference/lookup data (NUTS boundaries, TED codelists) and the derived
EEA station enrichment that depends on it are not re-ingested before
every scheduled update — re-downloading and re-deriving EU-wide reference
data on every run would be wasteful and these sources change rarely (see
docs/aws/architecture.md). Instead they're refreshed through a separate,
rare "bootstrap-reference" run, and this module writes/checks a small
completion manifest at system/bootstrap/reference/latest.json confirming
that run actually produced everything historical/update depend on.

Historical/update must call check_bootstrap_complete() before touching
main data — an incomplete or missing bootstrap manifest raises a clear
RuntimeError instead of letting the pipeline silently continue with empty
NUTS mappings or codelist labels. Presence of one or two files is not
enough: every path this module checks must exist in final storage for
the manifest to record status="COMPLETE".

    from common.bootstrap import write_bootstrap_manifest, check_bootstrap_complete
    write_bootstrap_manifest(storage_mode="cloud")   # after a successful bootstrap run
    check_bootstrap_complete(storage_mode="cloud")   # called by historical/update before real work
"""
import json
import logging
from datetime import datetime, timezone

from common.storage import exists, read_text, write_text
from ingestion.eea.nuts_boundaries import OUT_PATH as NUTS_BOUNDARIES_PATH
from ingestion.ted.codelists import CODELISTS as TED_CODELIST_IDS
from normalization.ted.codelists import OUT_DIR as TED_CODELISTS_NORMALIZED_DIR
from transformation.eea.stations import (
    STATION_METADATA_FILENAME,
    TRANSFORMED_BASE_DIR as EEA_STATIONS_TRANSFORMED_DIR,
)

logger = logging.getLogger(__name__)

MANIFEST_PATH = "system/bootstrap/reference/latest.json"

# The minimum reference outputs historical/update actually depend on —
# path constants imported from their owning modules rather than
# duplicated as string literals, so this stays in sync if a source
# changes its own output layout.
REQUIRED_PATHS = {
    "nuts_boundaries": NUTS_BOUNDARIES_PATH,
    "eea_stations_de": f"{EEA_STATIONS_TRANSFORMED_DIR}/DE/{STATION_METADATA_FILENAME}",
    "eea_stations_pl": f"{EEA_STATIONS_TRANSFORMED_DIR}/PL/{STATION_METADATA_FILENAME}",
}
REQUIRED_TED_CODELIST_PATHS = {
    f"ted_codelist_{codelist_id}": f"{TED_CODELISTS_NORMALIZED_DIR}/{codelist_id}.parquet"
    for codelist_id in TED_CODELIST_IDS
}


def _all_required_paths() -> dict[str, str]:
    paths = dict(REQUIRED_PATHS)
    paths.update(REQUIRED_TED_CODELIST_PATHS)
    return paths


def write_bootstrap_manifest(storage_mode: str = "local") -> dict:
    """
    Checks every required reference output against final storage right
    now and writes the result to MANIFEST_PATH — call this once a
    bootstrap-reference run's ingestion/normalization/transformation
    steps have all finished. Does not re-run anything itself, so a
    partially-failed bootstrap run is recorded as INCOMPLETE rather than
    silently marked done.
    """
    required = _all_required_paths()
    checks = {name: exists(path, storage_mode) for name, path in required.items()}
    missing = [name for name, ok in checks.items() if not ok]

    manifest = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "COMPLETE" if not missing else "INCOMPLETE",
        "checks": checks,
        "missing": missing,
    }

    write_text(MANIFEST_PATH, json.dumps(manifest, ensure_ascii=False, indent=2), storage_mode)

    if missing:
        logger.error("Bootstrap manifest written | status=INCOMPLETE missing=%s path=%s",
                     missing, MANIFEST_PATH)
    else:
        logger.info("Bootstrap manifest written | status=COMPLETE checks=%s path=%s",
                    len(checks), MANIFEST_PATH)
    return manifest


def check_bootstrap_complete(storage_mode: str = "local") -> dict:
    """
    Called by historical/update before processing any main data. Raises
    RuntimeError with a clear, actionable message if the bootstrap
    manifest is missing or recorded status != "COMPLETE" — never allows
    silent continuation with empty NUTS fields or codelist labels.
    """
    if not exists(MANIFEST_PATH, storage_mode):
        raise RuntimeError(
            f"Bootstrap completion manifest not found at {MANIFEST_PATH} — "
            f"run the bootstrap-reference workflow first (see docs/aws/operations.md)."
        )

    manifest = json.loads(read_text(MANIFEST_PATH, storage_mode))
    if manifest.get("status") != "COMPLETE":
        raise RuntimeError(
            f"Bootstrap is incomplete (status={manifest.get('status')!r}, "
            f"missing={manifest.get('missing')}) — re-run the bootstrap-reference "
            f"workflow before running historical/update (see docs/aws/operations.md)."
        )

    logger.info("Bootstrap completion verified | checked_at=%s", manifest.get("checked_at"))
    return manifest
