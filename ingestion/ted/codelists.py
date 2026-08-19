"""
Reference codelists ingestion — downloads eForms SDK codelists (Genericode
XML) from GitHub, saved exactly as received. No country logic — these are
EU-wide lookup tables, not scoped to any country. Parsing the XML into a
flat table happens in normalization.ted.codelists.

These are lookup/reference tables (code -> human-readable label), not raw
procurement facts — hence data/reference, not data/raw.

Source: https://github.com/OP-TED/eForms-SDK/tree/main/codelists
        (each codelist is one Genericode .gc XML file)

    from ingestion.ted.codelists import run
    run()

Requires: pip install requests
"""
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

OUT_DIR = Path("data/reference/ted/codelists")
OUT_DIR.mkdir(parents=True, exist_ok=True)

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


def run():
    """
    Entry point to be imported and called from main.py, e.g.:

        from ingestion.ted.codelists import run
        run()
    """
    results = {}
    for codelist_id, filename in CODELISTS.items():
        xml_bytes = fetch_codelist_xml(codelist_id, filename)
        if xml_bytes is None:
            logger.warning("Codelist skipped | codelist=%s — download failed, no file written",
                           codelist_id)
            continue

        out_path = OUT_DIR / f"{codelist_id}.gc.xml"
        out_path.write_bytes(xml_bytes)
        logger.info("Codelist saved | codelist=%s size_bytes=%s path=%s",
                    codelist_id, len(xml_bytes), out_path.resolve())
        results[codelist_id] = out_path

    logger.info("Codelist ingestion finished | saved=%s/%s", len(results), len(CODELISTS))
    if not results:
        logger.warning("No codelists were saved — see per-codelist errors above")
    return results


if __name__ == "__main__":
    run()
