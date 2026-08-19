"""
TED reference codelists normalization.

Reads the raw Genericode XML files saved by ingestion.ted.codelists and
parses each into a flat, joinable list of {column: value} rows.

    from normalization.ted.codelists import run
    run()
"""
import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

RAW_DIR = Path("data/reference/ted/codelists")
OUT_DIR = Path("data/normalized/ted/codelists")
OUT_DIR.mkdir(parents=True, exist_ok=True)


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


def run():
    if not RAW_DIR.exists():
        raise FileNotFoundError(
            f"No raw codelists directory at {RAW_DIR} — run ingestion.ted.codelists first."
        )

    results = {}
    for xml_path in sorted(RAW_DIR.glob("*.gc.xml")):
        codelist_id = xml_path.name.removesuffix(".gc.xml")
        rows = parse_genericode(xml_path.read_bytes())
        if not rows:
            logger.warning(
                "Codelist parsed 0 rows | codelist=%s — XML structure may not "
                "match expected Genericode format", codelist_id,
            )
            continue

        out_path = OUT_DIR / f"{codelist_id}.json"
        out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
        logger.info("Codelist normalized | codelist=%s rows=%s path=%s",
                    codelist_id, len(rows), out_path.resolve())
        results[codelist_id] = out_path

    logger.info("Codelist normalization finished | saved=%s", len(results))
    return results


if __name__ == "__main__":
    run()
