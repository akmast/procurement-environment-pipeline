"""
Eurostat regional agricultural accounts — Gold Layer.

Reads normalized JSON-stat-decoded Parquet files
(normalization.eurostat.agriculture_accounts — eurostat has no
transformation stage, see main.py's FAMILY_STAGES), keeps only the
columns useful for analysis, renames them to Gold's own naming (see
RENAME below — e.g. `geo` -> `nuts2`, this dataset's `geo` dimension is
always a NUTS2 region code such as "DE11"), and writes **one Gold file
per precursor partition** — mirroring normalization's own country/year
partitioning, not one combined file for the whole source (see
common/gold.py's module docstring for why: a run only reprocesses the
specific partition(s) its precursor actually touched, via
--input-manifest, exactly like normalization/transformation already
do — see docs/pipelines/gold_layer.md).

Dropped relative to the normalized table: the raw `unit` code (every
row in this dataset is `MIO_EUR`, per
docs/pipelines/eurostat_agriculture_accounts.md — `unit_label` alone is
enough) and `time_label` (a string echo of `time`, e.g. "2021" for
2021 — the typed `time` column already covers it).

Every output column is cast to a fixed dtype (GOLD_DTYPES) right before
write — never left to what the precursor file's own dtype happens to
be (see gold/eea/measurements.py's docstring for why that matters). A
row missing any column is meaningless here (there's no "count-only"
use case the way TED has) — REQUIRED_COLUMNS is every column except
frequency_code/frequency_label, see common.gold.drop_missing_required.

`countries` must be passed explicitly, same "explicit partitions only"
convention as every other stage in this project — pass
discover_countries(storage_mode) to (re)process every country
currently normalized (a full rebuild — see GoldStandardStateMachine,
docs/pipelines/gold_layer.md), or the exact precursor file paths from
this run's own normalization manifest (the normal AWS wiring — see
main.py's --input-manifest) to process only what actually changed.
Each partition's Gold file is simply overwritten in place — there's no
separate reconciliation step needed, unlike a scheme that only ever
appended new files.

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

from common.gold import build_gold_partition, drop_missing_required, enforce_dtypes, gold_partition_path, \
    write_gold_table
from common.manifest import StageResult
from common.storage import delete, exists, list_files, resolve_paths
from normalization.eurostat.agriculture_accounts import NORMALIZED_BASE_DIR

logger = logging.getLogger(__name__)

GOLD_BASE_DIR = "data/gold/eurostat"
GOLD_FILENAME_PREFIX = "agriculture_accounts"

# The single combined file this module used to write, before Gold moved
# to one file per precursor partition — see run()'s cleanup_legacy_file.
_LEGACY_GOLD_PATH = f"{GOLD_BASE_DIR}/agriculture_accounts.parquet"

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


def run(storage_mode: str = "local", countries: list[str] | None = None,
        cleanup_legacy_file: bool = False) -> StageResult:
    if not countries:
        raise ValueError(
            "countries must be provided explicitly — e.g. countries=['DE'], or "
            "countries=discover_countries(storage_mode) to (re)process every country "
            "already normalized. run() does not default to processing everything on disk."
        )

    logger.info("Starting Eurostat agricultural accounts Gold build | countries=%s storage_mode=%s",
                countries, storage_mode)

    if cleanup_legacy_file and exists(_LEGACY_GOLD_PATH, storage_mode):
        logger.info("Deleting legacy combined Gold file, superseded by per-partition files | path=%s",
                     _LEGACY_GOLD_PATH)
        delete(_LEGACY_GOLD_PATH, storage_mode)

    paths = resolve_paths(countries, NORMALIZED_BASE_DIR, storage_mode, suffix=".parquet")
    if not paths:
        logger.warning("No normalized agricultural accounts files found for countries=%s under %s",
                        countries, NORMALIZED_BASE_DIR)
        return StageResult().finalize(attempted=0)

    result = StageResult()
    for path in paths:
        try:
            df = build_gold_partition(path, storage_mode, SOURCE_COLUMNS, rename=RENAME)
            df = enforce_dtypes(df, GOLD_DTYPES)
            df = drop_missing_required(df, REQUIRED_COLUMNS)

            out_path = gold_partition_path(path, NORMALIZED_BASE_DIR, GOLD_BASE_DIR, GOLD_FILENAME_PREFIX)
            write_gold_table(df, out_path, storage_mode)
            result.record_written(out_path)
        except Exception:
            logger.exception("Eurostat agricultural accounts Gold build failed for partition | path=%s", path)
            result.record_failed(path)

    logger.info("Eurostat agricultural accounts Gold build finished | partitions=%s written=%s failed=%s",
                len(paths), len(result.written_paths), len(result.failed_paths))
    return result.finalize(attempted=len(paths))


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
        countries=["DE", "PL"],  # required — or discover_countries("local") for everything normalized
    )
