"""
EEA station metadata transformation — deduplication.

Reads the normalized (flattened, cleaned) station table produced by
normalization.eea.stations and deduplicates it by station code. Kept
separate from normalization because normalization's job is reshaping raw
data into readable structure — deciding which rows are "the same
station" and should collapse into one is a heavier, more opinionated
step that belongs here.

Reads/writes go through common.storage, so storage_mode="local" (default)
and storage_mode="cloud" (S3) run the same logic.

    from transformation.eea.stations import run
    run()
    run(storage_mode="cloud")
"""
import logging
import sys
from io import BytesIO
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.storage import exists, read_bytes, write_bytes

logger = logging.getLogger(__name__)

NORMALIZED_PATH = "data/normalized/eea/stations/station_metadata.parquet"
STATION_METADATA_PATH = "data/transformed/eea/stations/station_metadata.parquet"


def deduplicate_stations(df: pd.DataFrame) -> pd.DataFrame:
    before = len(df)
    df = df.drop_duplicates(subset=["AirQualityStationEoICode"])
    logger.info("Deduplicated stations | %s -> %s rows", before, len(df))
    return df


def run(storage_mode: str = "local"):
    logger.info("Starting EEA station metadata transformation | storage_mode=%s", storage_mode)

    if not exists(NORMALIZED_PATH, storage_mode):
        raise FileNotFoundError(
            f"No normalized stations file at {NORMALIZED_PATH} — "
            f"run normalization.eea.stations first."
        )

    df = pd.read_parquet(BytesIO(read_bytes(NORMALIZED_PATH, storage_mode)))
    df = deduplicate_stations(df)

    buffer = BytesIO()
    df.to_parquet(buffer, index=False)
    write_bytes(STATION_METADATA_PATH, buffer.getvalue(), storage_mode)

    logger.info("Transformed stations saved | path=%s rows=%s",
                STATION_METADATA_PATH, len(df))


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
    )
