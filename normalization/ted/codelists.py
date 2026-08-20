"""
TED reference codelists normalization.

Reads the raw Genericode XML files saved by ingestion.ted.codelists and
parses each into a flat, joinable list of {column: value} rows.

Reads/writes go through common.storage, so storage_mode="local" (default)
and storage_mode="cloud" (S3) run the same logic.

    from normalization.ted.codelists import run
    run()
    run(storage_mode="cloud")
"""
import json
import logging
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.storage import list_files, read_bytes, write_text

logger = logging.getLogger(__name__)

RAW_DIR = "data/reference/ted/codelists"
OUT_DIR = "data/normalized/ted/codelists"


def parse_genericode(xml_bytes: bytes) -> list[dict]:
    """
    Parse a Genericode XML file into a list of {column: value} dicts.

    Quirk: only the root <gc:CodeList> element carries the gc: namespace
    prefix. Everything below it (ColumnSet, Column, Row, Value...) is
    unprefixed / in no namespace — so we search for those WITHOUT the
    gc: prefix, even though we're inside a gc:-namespaced root.
    """
    root = ET.fromstring(xml_bytes)

    columns = [
        col.attrib["Id"]
        for col in root.findall(".//ColumnSet/Column")
    ]

    rows_out = []
    row_elements = root.findall(".//SimpleCodeList/Row")
    logger.debug("Genericode parsed | columns=%s rows=%s", columns, len(row_elements))

    for row in row_elements:
        row_dict = {}
        for i, value_el in enumerate(row.findall("Value")):
            simple_value = value_el.find("SimpleValue")
            col_id = columns[i] if i < len(columns) else f"col_{i}"
            row_dict[col_id] = simple_value.text if simple_value is not None else None
        rows_out.append(row_dict)

    return rows_out


def run(storage_mode: str = "local"):
    logger.info("Starting TED codelists normalization | storage_mode=%s", storage_mode)

    xml_files = list_files(RAW_DIR, storage_mode, suffix=".gc.xml")
    if not xml_files:
        raise FileNotFoundError(
            f"No raw codelist files found under {RAW_DIR} — run ingestion.ted.codelists first."
        )

    results = {}
    for xml_path in xml_files:
        filename = xml_path.rsplit("/", 1)[-1]
        codelist_id = filename.removesuffix(".gc.xml")
        rows = parse_genericode(read_bytes(xml_path, storage_mode))
        if not rows:
            logger.warning(
                "Codelist parsed 0 rows | codelist=%s — XML structure may not "
                "match expected Genericode format", codelist_id,
            )
            continue

        out_path = f"{OUT_DIR}/{codelist_id}.json"
        write_text(out_path, json.dumps(rows, ensure_ascii=False, indent=2), storage_mode)
        logger.info("Codelist normalized | codelist=%s rows=%s path=%s",
                    codelist_id, len(rows), out_path)
        results[codelist_id] = out_path

    logger.info("Codelist normalization finished | saved=%s", len(results))
    return results


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
    )
