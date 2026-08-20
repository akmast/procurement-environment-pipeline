"""
TED reference codelists normalization.

Reads the raw Genericode XML files saved by ingestion.ted.codelists and
parses each into a flat, joinable table, written to Parquet — the same
format as every other normalized dataset in this project, and directly
loadable by transformation.ted.notices for its code -> label joins
(notice_type, buyer_country, total_value_currency,
winner_selection_status, non_award_justification, nuts, classification
CPV codes).

Each row keeps every column Genericode provides for that code — the
"code" column itself, plus a generic "Name" column and one
"<lang>_label" column per language (confirmed live: bul_label,
spa_label, ..., deu_label, eng_label, ...). Normalization doesn't pick a
single label here — which one to prefer is an opinionated choice left to
whatever joins against it (see transformation.ted.notices).

Reads/writes go through common.storage, so storage_mode="local" (default)
and storage_mode="cloud" (S3) run the same logic.

    from normalization.ted.codelists import run
    run()
    run(storage_mode="cloud")
"""
import logging
import sys
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.storage import list_files, read_bytes, write_bytes

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

        df = pd.DataFrame(rows)
        out_path = f"{OUT_DIR}/{codelist_id}.parquet"
        buffer = BytesIO()
        df.to_parquet(buffer, index=False)
        write_bytes(out_path, buffer.getvalue(), storage_mode)

        logger.info("Codelist normalized | codelist=%s rows=%s columns=%s path=%s",
                    codelist_id, len(df), list(df.columns), out_path)
        results[codelist_id] = out_path

    logger.info("Codelist normalization finished | saved=%s", len(results))
    return results


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
    )
