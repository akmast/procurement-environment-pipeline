"""
EEA air quality measurements — Gold Layer.

Reads every transformed measurements Parquet file
(transformation.eea.measurements — already has station location/NUTS
codes joined in) across every country/year/pollutant currently on disk,
concatenates them into one table, keeps only the columns useful for
analysis, renames them to Gold's own naming (see RENAME below — e.g.
`nuts1_code`/`nuts2_code`/`nuts3_code` -> `nuts1`/`nuts2`/`nuts3`,
matching the plain `nuts`/`nuts1`/`nuts2`/`nuts3` naming the TED Gold
table uses), deduplicates exact repeat rows, and writes ONE combined
file — no country/year/pollutant split — to
data/gold/eea/measurements.parquet.

Dropped relative to the transformed table: `aggregation_type` (not
needed for Gold-level analysis).

Every output column is cast to a fixed dtype (GOLD_DTYPES) right before
write — never left to what pandas/pyarrow infer from concatenating
partition files, which can silently drift (e.g. `pollutant_code` is an
EEA vocabulary code, kept as a string; earlier code cast it numeric,
which combined with one drifted partition file to produce inconsistent
types in the real Gold file — Athena then failed reading it against the
Glue table's `bigint` definition with `HIVE_BAD_DATA`). Rows missing
any of REQUIRED_COLUMNS are dropped (see drop_missing_required) —
`validity_code` is required because an unvalidated measurement isn't
meaningful for analysis; `verification_code` is not (see
docs/pipelines/gold_layer.md).

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

    from gold.eea.measurements import run, discover_countries
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
from transformation.eea.measurements import TRANSFORMED_BASE_DIR

logger = logging.getLogger(__name__)

GOLD_BASE_DIR = "data/gold/eea"
GOLD_FILENAME = "measurements.parquet"

# Source-column order — matches transformation.eea.measurements's output
# columns exactly (KEEP_COLUMNS + the station join), minus
# `aggregation_type` (see module docstring for why).
SOURCE_COLUMNS = [
    "country_code", "sampling_point", "pollutant", "period_start", "period_end",
    "value", "unit", "validity", "verification", "result_time", "location",
    "nuts1_code", "nuts2_code", "nuts3_code",
]
RENAME = {
    "sampling_point": "sampling_point_id",
    "pollutant": "pollutant_code",
    "period_start": "measurement_period_start",
    "period_end": "measurement_period_end",
    "value": "measurement_value",
    "unit": "measurement_unit",
    "validity": "validity_code",
    "verification": "verification_code",
    "result_time": "result_timestamp",
    "location": "station_location",
    "nuts1_code": "nuts1",
    "nuts2_code": "nuts2",
    "nuts3_code": "nuts3",
}

# Enforced right before write (see common.gold.enforce_dtypes) — matches
# infrastructure/terraform/glue.tf's eea_measurements table column-for-
# column. pollutant_code/validity_code/verification_code are EEA
# vocabulary codes, kept/dropped as strings/nullable ints respectively,
# never guessed from what a partition file happens to contain.
GOLD_DTYPES = {
    "country_code": "string",
    "sampling_point_id": "string",
    "pollutant_code": "string",
    "measurement_period_start": "datetime64[ns]",
    "measurement_period_end": "datetime64[ns]",
    "measurement_value": "float64",
    "measurement_unit": "string",
    "validity_code": "Int64",
    "verification_code": "Int64",
    "result_timestamp": "datetime64[ns]",
    "station_location": "string",
    "nuts1": "string",
    "nuts2": "string",
    "nuts3": "string",
}

# A measurement missing any of these isn't meaningful for analysis — see
# common.gold.drop_missing_required. verification_code is deliberately
# NOT required: an unverified-but-validated measurement is still usable.
REQUIRED_COLUMNS = [
    "country_code",
    "pollutant_code",
    "measurement_period_start",
    "measurement_value",
    "measurement_unit",
    "validity_code",
]


def discover_countries(storage_mode: str) -> list[str]:
    """Country codes come from the transformed layer's own <country>/ subdirectories."""
    transformed_files = list_files(TRANSFORMED_BASE_DIR, storage_mode, suffix=".parquet")
    return sorted({path[len(TRANSFORMED_BASE_DIR):].lstrip("/").split("/")[0] for path in transformed_files})


def run(storage_mode: str = "local", countries: list[str] | None = None) -> StageResult:
    if not countries:
        raise ValueError(
            "countries must be provided explicitly — e.g. countries=['DE'], or "
            "countries=discover_countries(storage_mode) to combine every country "
            "already transformed. run() does not default to processing everything on disk."
        )

    logger.info("Starting EEA measurements Gold build | countries=%s storage_mode=%s", countries, storage_mode)

    paths = resolve_paths(countries, TRANSFORMED_BASE_DIR, storage_mode, suffix=".parquet")
    if not paths:
        logger.warning("No transformed measurements files found for countries=%s under %s",
                        countries, TRANSFORMED_BASE_DIR)
        return StageResult().finalize(attempted=0)

    df = build_gold_table(paths, storage_mode, SOURCE_COLUMNS, rename=RENAME)
    df = enforce_dtypes(df, GOLD_DTYPES)
    df = drop_missing_required(df, REQUIRED_COLUMNS)

    out_path = f"{GOLD_BASE_DIR}/{GOLD_FILENAME}"
    write_gold_table(df, out_path, storage_mode)

    result = StageResult()
    result.record_written(out_path)
    logger.info("EEA measurements Gold build finished | source_files=%s rows=%s path=%s",
                len(paths), len(df), out_path)
    return result.finalize(attempted=len(paths))


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
        countries=["DE", "PL"],  # required — or discover_countries("local") for everything transformed
    )
