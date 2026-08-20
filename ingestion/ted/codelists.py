"""
Reference codelists ingestion — downloads eForms SDK codelists (Genericode
XML) from GitHub, saved exactly as received. No country logic — these are
EU-wide lookup tables, not scoped to any country. Parsing the XML into a
flat table happens in normalization.ted.codelists.

These are lookup/reference tables (code -> human-readable label), not raw
procurement facts — hence data/reference, not data/raw.

Source: https://github.com/OP-TED/eForms-SDK/tree/main/codelists
        (each codelist is one Genericode .gc XML file)

Reads/writes go through common.storage, so storage_mode="local" (default)
and storage_mode="cloud" (S3) run the exact same logic. Each codelist is
staged, validated as well-formed XML, and only then hash-compared
against what's already stored (see common/staged_write.py) — written to
final storage only if both checks pass. GitHub doesn't republish these
often, so most runs should find nothing changed.

    from ingestion.ted.codelists import run
    run()
    run(storage_mode="cloud")

Requires: pip install requests
"""
import logging
import sys
from pathlib import Path

import requests

# Make `common` importable and logging configured regardless of how this
# file is run (python -m, import, Jupyter, or run directly).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.logging_config import setup_logging
from common.change_tracking import load_state, save_state
from common.staged_write import stage_validate_and_write
from common.validation import is_valid_xml

setup_logging()
logger = logging.getLogger(__name__)

OUT_DIR = "data/reference/ted/codelists"
STATE_PATH = f"{OUT_DIR}/state.json"

RAW_BASE = "https://raw.githubusercontent.com/OP-TED/eForms-SDK/main/codelists"

# Codelist IDs we actually use in our TED fields. Verify each filename by
# browsing https://github.com/OP-TED/eForms-SDK/tree/main/codelists first —
# genericode filenames don't always match the field name exactly, and this
# is a guess list to start from, not a guaranteed-correct one.
CODELISTS = {
    "notice-type": "notice-type.gc",
    "winner-selection-status": "winner-selection-status.gc",
    "non-award-justification": "non-award-justification.gc",  # BT-144 reason codes
    "country": "country.gc",      # decodes buyer-country ("DEU" -> Germany)
    "currency": "currency.gc",    # decodes total-value-cur ("EUR" -> Euro)
    "cpv": "cpv.gc",               # decodes classification-cpv — this one is
                                    # big (~9000 codes), give it more time
    "nuts": "nuts.gc",             # decodes place-of-performance / BT-5071-Lot
                                    # NUTS3 codes ("DE712" -> region name).
                                    # NOTE: filename is a best guess by naming
                                    # convention, not confirmed like the others
                                    # — if this 404s, that's expected, we'll
                                    # need to find the real filename.
}


def fetch_codelist_xml(codelist_id: str, filename: str) -> bytes | None:
    url = f"{RAW_BASE}/{filename}"
    resp = requests.get(url, timeout=30)
    logger.info("Codelist requested | codelist=%s status=%s size_bytes=%s",
                codelist_id, resp.status_code, len(resp.content))

    if not resp.ok:
        logger.error(
            "Codelist download failed | codelist=%s status=%s — check the exact "
            "filename at https://github.com/OP-TED/eForms-SDK/tree/main/codelists",
            codelist_id, resp.status_code,
        )
        return None

    return resp.content


def run(storage_mode: str = "local"):
    """
    Entry point to be imported and called from main.py, e.g.:

        from ingestion.ted.codelists import run
        run()
    """
    logger.info("Starting TED codelists ingestion | storage_mode=%s", storage_mode)
    state = load_state(STATE_PATH, storage_mode)

    results = {}
    written = 0
    for codelist_id, filename in CODELISTS.items():
        xml_bytes = fetch_codelist_xml(codelist_id, filename)
        if xml_bytes is None:
            logger.warning("Codelist skipped | codelist=%s — download failed, no file written",
                           codelist_id)
            continue

        out_path = f"{OUT_DIR}/{codelist_id}.gc.xml"
        is_written = stage_validate_and_write(
            out_path, xml_bytes, storage_mode, state, validate=is_valid_xml
        )
        if not is_written:
            logger.info("Codelist not written (unchanged or invalid) | codelist=%s", codelist_id)
            results[codelist_id] = out_path
            continue

        written += 1
        logger.info("Codelist saved | codelist=%s size_bytes=%s path=%s",
                    codelist_id, len(xml_bytes), out_path)
        results[codelist_id] = out_path

    save_state(STATE_PATH, state, storage_mode)

    logger.info("Codelist ingestion finished | checked=%s/%s written=%s",
                len(results), len(CODELISTS), written)
    if not results:
        logger.warning("No codelists were saved — see per-codelist errors above")
    return results


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
    )
