"""
Eurostat regional agricultural accounts — Gold Layer.

Reads every normalized JSON-stat-decoded Parquet file across every
country/year currently on disk
(normalization.eurostat.agriculture_accounts — eurostat has no
transformation stage, see main.py's FAMILY_STAGES), concatenates them
into one table, keeps only the columns useful for analysis, renames
them to Gold's own naming (see RENAME below — e.g. `geo` -> `nuts2`,
this dataset's `geo` dimension is always a NUTS2 region code such as
"DE11"), deduplicates exact repeat rows, and writes ONE combined file —
no country/year split — to data/gold/eurostat/agriculture_accounts.parquet.

Dropped relative to the normalized table: the raw `unit` code (every
row in this dataset is `MIO_EUR`, per
docs/pipelines/eurostat_agriculture_accounts.md — `unit_label` alone is
enough) and `time_label` (a string echo of `time`, e.g. "2021" for
2021 — the typed `time` column already covers it).

Every output column is cast to a fixed dtype (GOLD_DTYPES) right before
write — never left to what pandas/pyarrow infer from concatenating
partition files (see gold/eea/measurements.py's docstring for why that
matters). A row missing any column is meaningless here (there's no
"count-only" use case the way TED has) — REQUIRED_COLUMNS is every
column except frequency_code/frequency_label, see
common.gold.drop_missing_required.

`countries` must be passed explicitly, same "explicit partitions only"
convention as every other stage in this project — pass
discover_countries(storage_mode) to combine every country currently
normalized. Unlike normalization, Gold Layer always *rebuilds the whole
combined file* from the countries given (there's no partition of its
own to merge into) — passing a partial `countries` list here means the
combined file only reflects those countries, not "these plus whatever
was already there".

Reads/writes go through common.storage, so storage_mode="local"
(default) and storage_mode="cloud" (S3) run the same logic.

    from gold.eurostat.agriculture_accounts import run, discover_countries
    run(countries=["DE", "PL"])
    run(countries=discover_countries("local"))
    run(countries=["DE"], storage_mode="cloud")
"""
import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.gold import build_gold_table, drop_missing_required, enforce_dtypes, write_gold_table
from common.manifest import StageResult
from common.storage import list_files, resolve_paths
from normalization.eurostat.agriculture_accounts import NORMALIZED_BASE_DIR

logger = logging.getLogger(__name__)

GOLD_BASE_DIR = "data/gold/eurostat"
GOLD_FILENAME = "agriculture_accounts.parquet"

# Source-column order — matches normalization.eurostat.agriculture_accounts's
# output columns exactly (see that module's flatten/melt_json_stat), minus
# `unit` and `time_label` (see module docstring for why).
SOURCE_COLUMNS = [
    "country_code", "freq", "freq_label", "am_item", "am_item_label",
    "indic_agr", "indic_agr_label", "unit_label", "geo", "geo_label", "time", "value",
]
RENAME = {
    "freq": "frequency_code",
    "freq_label": "frequency_label",
    "am_item": "agricultural_item_code",
    "am_item_label": "agricultural_item_label",
    "indic_agr": "agricultural_indicator_code",
    "indic_agr_label": "agricultural_indicator_label",
    "geo": "nuts2",
    "geo_label": "nuts2_label",
    "time": "reference_year",
    "value": "indicator_value",
}

# Enforced right before write (see common.gold.enforce_dtypes) — matches
# infrastructure/terraform/glue.tf's eurostat_agriculture_accounts table
# column-for-column. Every code/label/NUTS field is a categorical
# identifier, kept as a string even where it looks numeric.
GOLD_DTYPES = {
    "country_code": "string",
    "frequency_code": "string",
    "frequency_label": "string",
    "agricultural_item_code": "string",
    "agricultural_item_label": "string",
    "agricultural_indicator_code": "string",
    "agricultural_indicator_label": "string",
    "unit_label": "string",
    "nuts2": "string",
    "nuts2_label": "string",
    "reference_year": "Int64",
    "indicator_value": "float64",
}

# Every column except frequency_code/frequency_label — see module
# docstring and common.gold.drop_missing_required.
REQUIRED_COLUMNS = [
    "country_code",
    "agricultural_item_code",
    "agricultural_item_label",
    "agricultural_indicator_code",
    "agricultural_indicator_label",
    "unit_label",
    "nuts2",
    "nuts2_label",
    "reference_year",
    "indicator_value",
]


def discover_countries(storage_mode: str) -> list[str]:
    """Country codes come from the normalized layer's own <country>/ subdirectories."""
    normalized_files = list_files(NORMALIZED_BASE_DIR, storage_mode, suffix=".parquet")
    return sorted({path[len(NORMALIZED_BASE_DIR):].lstrip("/").split("/")[0] for path in normalized_files})


def run(storage_mode: str = "local", countries: list[str] | None = None) -> StageResult:
    if not countries:
        raise ValueError(
            "countries must be provided explicitly — e.g. countries=['DE'], or "
            "countries=discover_countries(storage_mode) to combine every country "
            "already normalized. run() does not default to processing everything on disk."
        )

    logger.info("Starting Eurostat agricultural accounts Gold build | countries=%s storage_mode=%s",
                countries, storage_mode)

    paths = resolve_paths(countries, NORMALIZED_BASE_DIR, storage_mode, suffix=".parquet")
    if not paths:
        logger.warning("No normalized agricultural accounts files found for countries=%s under %s",
                        countries, NORMALIZED_BASE_DIR)
        return StageResult().finalize(attempted=0)

    df = build_gold_table(paths, storage_mode, SOURCE_COLUMNS, rename=RENAME)
    df = enforce_dtypes(df, GOLD_DTYPES)
    df = drop_missing_required(df, REQUIRED_COLUMNS)

    out_path = f"{GOLD_BASE_DIR}/{GOLD_FILENAME}"
    write_gold_table(df, out_path, storage_mode)

    result = StageResult()
    result.record_written(out_path)
    logger.info("Eurostat agricultural accounts Gold build finished | source_files=%s rows=%s path=%s",
                len(paths), len(df), out_path)
    return result.finalize(attempted=len(paths))


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
        countries=["DE", "PL"],  # required — or discover_countries("local") for everything normalized
    )
