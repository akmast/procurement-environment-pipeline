"""
TED procurement notices — Gold Layer.

Reads every transformed notices Parquet file (transformation.ted.notices
— already deduplicated and codelist-labeled, one file per country, no
year partitioning for this source) across every country currently on
disk, concatenates them into one table, keeps only the columns useful
for analysis, renames them to Gold's own naming (see RENAME below —
e.g. `publication_number` -> `notice_publication_number`, `nuts` ->
`place_of_performance_nuts`), deduplicates exact repeat rows, and
writes ONE combined file — no country split — to
data/gold/ted/notices.parquet.

Kept from the transformed table: the core notice identity/value fields
plus NUTS codes and their labels (`nuts`/`nuts1`/`nuts2`/`nuts3`,
`nuts_label`/`nuts1_label`). List-valued fields from normalization
(buyer_country, classification_cpv, place_of_performance_country,
green_procurement_criteria) and the other codelist labels are
deliberately left out of this table — not needed for Gold-level
analysis, and none of them collide with drop_duplicates() (lists are
unhashable, so keeping them would break exact-row deduplication).

`countries` must be passed explicitly, same "explicit partitions only"
convention as every other stage in this project — pass
discover_countries(storage_mode) to combine every country currently
transformed. Unlike transformation, Gold Layer always *rebuilds the
whole combined file* from the countries given (there's no partition of
its own to merge into) — passing a partial `countries` list here means
the combined file only reflects those countries, not "these plus
whatever was already there".

Reads/writes go through common.storage, so storage_mode="local"
(default) and storage_mode="cloud" (S3) run the same logic.

    from gold.ted.notices import run, discover_countries
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

from common.gold import build_gold_table, write_gold_table
from common.manifest import StageResult
from common.storage import list_files, resolve_paths
from transformation.ted.notices import NOTICES_FILENAME, TRANSFORMED_BASE_DIR

logger = logging.getLogger(__name__)

GOLD_BASE_DIR = "data/gold/ted"
GOLD_FILENAME = "notices.parquet"

# Source-column order — a subset of transformation.ted.notices's output
# columns (see that module's add_codelist_labels/deduplicate_notices).
SOURCE_COLUMNS = [
    "country_code", "publication_number", "publication_date", "contract_conclusion_date",
    "buyer_name", "total_value", "total_value_currency",
    "nuts", "nuts1", "nuts2", "nuts3", "nuts_label", "nuts1_label",
]
RENAME = {
    "publication_number": "notice_publication_number",
    "publication_date": "notice_publication_date",
    "total_value": "contract_total_value",
    "total_value_currency": "contract_currency_code",
    "nuts": "place_of_performance_nuts",
    "nuts_label": "place_of_performance_nuts_label",
}


def discover_countries(storage_mode: str) -> list[str]:
    """Country codes come from the transformed layer's own <country>/ subdirectories."""
    transformed_files = list_files(TRANSFORMED_BASE_DIR, storage_mode, suffix=NOTICES_FILENAME)
    return sorted({path[len(TRANSFORMED_BASE_DIR):].lstrip("/").split("/")[0] for path in transformed_files})


def run(storage_mode: str = "local", countries: list[str] | None = None) -> StageResult:
    if not countries:
        raise ValueError(
            "countries must be provided explicitly — e.g. countries=['DE'], or "
            "countries=discover_countries(storage_mode) to combine every country "
            "already transformed. run() does not default to processing everything on disk."
        )

    logger.info("Starting TED notices Gold build | countries=%s storage_mode=%s", countries, storage_mode)

    paths = resolve_paths(countries, TRANSFORMED_BASE_DIR, storage_mode, suffix=".parquet")
    if not paths:
        logger.warning("No transformed notices files found for countries=%s under %s",
                        countries, TRANSFORMED_BASE_DIR)
        return StageResult().finalize(attempted=0)

    df = build_gold_table(paths, storage_mode, SOURCE_COLUMNS, rename=RENAME)

    out_path = f"{GOLD_BASE_DIR}/{GOLD_FILENAME}"
    write_gold_table(df, out_path, storage_mode)

    result = StageResult()
    result.record_written(out_path)
    logger.info("TED notices Gold build finished | source_files=%s rows=%s path=%s",
                len(paths), len(df), out_path)
    return result.finalize(attempted=len(paths))


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
        countries=["DE", "PL"],  # required — or discover_countries("local") for everything transformed
    )
