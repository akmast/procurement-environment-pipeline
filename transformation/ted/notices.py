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
   (see normalization.ted.codelists) for every coded field that's
   actually useful to interpret: notice_type, buyer_country,
   total_value_currency, winner_selection_status,
   non_award_justification, nuts/nuts1/nuts2/nuts3 (all four resolved
   from the single "nuts" codelist, which lists every NUTS level), and
   each code in classification_cpv / place_of_performance_country. This
   is the reason TED codelists get their own normalization stage — they
   exist to make these joins possible. Each codelist is loaded once per
   run and reused for every country. Not every coded field gets a join —
   see the "codelist coverage" note in docs/pipelines/ted_notices.md for
   which ones don't have a downloaded codelist to join against yet.

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

# (source column in notices, codelist_id to join against, output label column).
# A list, not a dict, because several rows share the same codelist:
# nuts.gc lists all NUTS levels (0-3) in one table, so the same lookup
# resolves nuts/nuts1/nuts2/nuts3 — genuinely useful for region-level
# aggregation at any granularity, not added just because the codelist
# exists.
CODELIST_JOINS = [
    ("notice_type", "notice-type", "notice_type_label"),
    ("buyer_country", "country", "buyer_country_label"),
    ("total_value_currency", "currency", "total_value_currency_label"),
    ("winner_selection_status", "winner-selection-status", "winner_selection_status_label"),
    ("non_award_justification", "non-award-justification", "non_award_justification_label"),
    ("nuts", "nuts", "nuts_label"),
    ("nuts1", "nuts", "nuts1_label"),
    ("nuts2", "nuts", "nuts2_label"),
    ("nuts3", "nuts", "nuts3_label"),
]

# List-column joins (classification_cpv, place_of_performance_country) —
# one label per code, same order, handled separately from the
# single-code joins above (see add_codelist_labels).
CPV_CODELIST_ID = "cpv"
PLACE_COUNTRY_CODELIST_ID = "country"

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
    codelist_ids = {codelist_id for _, codelist_id, _ in CODELIST_JOINS}
    codelist_ids |= {CPV_CODELIST_ID, PLACE_COUNTRY_CODELIST_ID}
    return {codelist_id: build_codelist_lookup(codelist_id, storage_mode) for codelist_id in codelist_ids}


def deduplicate_notices(df: pd.DataFrame) -> pd.DataFrame:
    before = len(df)
    df = df.drop_duplicates(subset=["publication_number"])
    logger.info("Deduplicated notices | %s -> %s rows", before, len(df))
    return df


def _label_list_column(series: pd.Series, lookup: dict) -> pd.Series:
    return series.apply(
        lambda codes: [lookup.get(code) for code in codes]
        if isinstance(codes, (list, np.ndarray)) else []
    )


def add_codelist_labels(df: pd.DataFrame, lookups: dict[str, dict]) -> pd.DataFrame:
    for source_column, codelist_id, label_column in CODELIST_JOINS:
        df[label_column] = df[source_column].map(lookups.get(codelist_id, {}))

    df["classification_cpv_labels"] = _label_list_column(
        df["classification_cpv"], lookups.get(CPV_CODELIST_ID, {})
    )
    df["place_of_performance_country_labels"] = _label_list_column(
        df["place_of_performance_country"], lookups.get(PLACE_COUNTRY_CODELIST_ID, {})
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
