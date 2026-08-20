"""
TED notices transformation — dedup + codelist labeling.

Reads the normalized notices table (one Parquet file per country, see
normalization.ted.notices) and:

1. Deduplicates by publication_number (a defensive safety net —
   ingestion already dedups on write, see ingestion.ted.notices — kept
   here for the same reason transformation.eea.stations re-dedups
   AirQualityStationEoICode: a cheap, deterministic guarantee that
   doesn't rely on every upstream step staying bug-free).
2. Joins in human-readable labels from the normalized TED codelists
   (see normalization.ted.codelists) for every coded field: notice_type,
   buyer_country, total_value_currency, winner_selection_status,
   non_award_justification, nuts, and each code in classification_cpv.
   This is the reason TED codelists get their own normalization stage —
   they exist to make these joins possible. Each codelist is loaded once
   per run and reused for every country.

Label preference per codelist row: deu_label (matches this project's
language convention elsewhere) -> eng_label -> Name -> the code itself
if no label column is usable. A code with no match in its codelist (or
a codelist that failed to load) gets a null label rather than failing
the run — labeling is an enrichment, not a required field.

If `countries` isn't passed, every country already normalized is
processed — read from the normalized layer's own directory structure.

Reads/writes go through common.storage, so storage_mode="local"
(default) and storage_mode="cloud" (S3) run the same logic.

    from transformation.ted.notices import run
    run()
    run(countries=["DE", "PL"])
    run(storage_mode="cloud")
"""
import logging
import sys
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.storage import exists, list_files, read_bytes, write_bytes

logger = logging.getLogger(__name__)

NORMALIZED_BASE_DIR = "data/normalized/ted"
TRANSFORMED_BASE_DIR = "data/transformed/ted"
NOTICES_FILENAME = "notices.parquet"
CODELISTS_BASE_DIR = "data/normalized/ted/codelists"

# codelist_id -> (source column in notices, output label column)
CODELIST_JOINS = {
    "notice-type": ("notice_type", "notice_type_label"),
    "country": ("buyer_country", "buyer_country_label"),
    "currency": ("total_value_currency", "total_value_currency_label"),
    "winner-selection-status": ("winner_selection_status", "winner_selection_status_label"),
    "non-award-justification": ("non_award_justification", "non_award_justification_label"),
    "nuts": ("nuts", "nuts_label"),
}
# classification_cpv is a list column, not a single code — labeled separately (see label_cpv_codes).
CPV_CODELIST_ID = "cpv"

# Preference order for which of a codelist row's columns to use as its
# human-readable label — not every codelist is guaranteed to have every
# column, so the first one present wins.
LABEL_COLUMN_PREFERENCE = ["deu_label", "eng_label", "Name", "code"]


def discover_countries(storage_mode: str) -> list[str]:
    """Country codes come from the normalized layer's own <country>/ subdirectories."""
    normalized_files = list_files(NORMALIZED_BASE_DIR, storage_mode, suffix=NOTICES_FILENAME)
    return sorted({Path(p).parent.name for p in normalized_files})


def build_codelist_lookup(codelist_id: str, storage_mode: str) -> dict:
    path = f"{CODELISTS_BASE_DIR}/{codelist_id}.parquet"
    if not exists(path, storage_mode):
        logger.warning("Codelist not found, labels will be left empty | codelist=%s path=%s",
                       codelist_id, path)
        return {}

    df = pd.read_parquet(BytesIO(read_bytes(path, storage_mode)))
    if "code" not in df.columns:
        logger.warning("Codelist has no 'code' column, labels will be left empty | codelist=%s columns=%s",
                       codelist_id, list(df.columns))
        return {}

    label_column = next((c for c in LABEL_COLUMN_PREFERENCE if c in df.columns), None)
    if label_column is None:
        logger.warning("Codelist has no usable label column, labels will be left empty | codelist=%s columns=%s",
                       codelist_id, list(df.columns))
        return {}

    return dict(zip(df["code"], df[label_column]))


def load_codelist_lookups(storage_mode: str) -> dict[str, dict]:
    codelist_ids = list(CODELIST_JOINS) + [CPV_CODELIST_ID]
    return {codelist_id: build_codelist_lookup(codelist_id, storage_mode) for codelist_id in codelist_ids}


def deduplicate_notices(df: pd.DataFrame) -> pd.DataFrame:
    before = len(df)
    df = df.drop_duplicates(subset=["publication_number"])
    logger.info("Deduplicated notices | %s -> %s rows", before, len(df))
    return df


def add_codelist_labels(df: pd.DataFrame, lookups: dict[str, dict]) -> pd.DataFrame:
    for codelist_id, (source_column, label_column) in CODELIST_JOINS.items():
        df[label_column] = df[source_column].map(lookups.get(codelist_id, {}))

    cpv_lookup = lookups.get(CPV_CODELIST_ID, {})
    df["classification_cpv_labels"] = df["classification_cpv"].apply(
        lambda codes: [cpv_lookup.get(code) for code in codes]
        if isinstance(codes, (list, np.ndarray)) else []
    )
    return df


def run(storage_mode: str = "local", countries: list[str] | None = None):
    countries = countries or discover_countries(storage_mode)
    if not countries:
        logger.warning("No normalized notices files found under %s", NORMALIZED_BASE_DIR)
        return

    logger.info("Starting TED notices transformation | countries=%s storage_mode=%s", countries, storage_mode)

    # Codelists are EU-wide reference data, not per-country — loaded once
    # and reused for every country's notices below.
    lookups = load_codelist_lookups(storage_mode)

    for country in countries:
        normalized_path = f"{NORMALIZED_BASE_DIR}/{country}/{NOTICES_FILENAME}"
        if not exists(normalized_path, storage_mode):
            raise FileNotFoundError(
                f"No normalized notices file at {normalized_path} — "
                f"run normalization.ted.notices first."
            )

        df = pd.read_parquet(BytesIO(read_bytes(normalized_path, storage_mode)))
        df = deduplicate_notices(df)
        df = add_codelist_labels(df, lookups)

        out_path = f"{TRANSFORMED_BASE_DIR}/{country}/{NOTICES_FILENAME}"
        buffer = BytesIO()
        df.to_parquet(buffer, index=False)
        write_bytes(out_path, buffer.getvalue(), storage_mode)

        logger.info("Transformed notices saved | country=%s path=%s rows=%s",
                    country, out_path, len(df))


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
        countries=["DE"],      # e.g. ["DE", "PL"] — omit/None to auto-discover from data/normalized/ted/
    )
