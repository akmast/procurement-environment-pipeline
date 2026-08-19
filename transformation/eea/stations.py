"""
EEA station metadata transformation — deduplication.

Reads the normalized (flattened, cleaned) station table produced by
normalization.eea.stations and deduplicates it by station code. Kept
separate from normalization because normalization's job is reshaping raw
data into readable structure — deciding which rows are "the same
station" and should collapse into one is a heavier, more opinionated
step that belongs here.

    from transformation.eea.stations import run
    run()
"""
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

NORMALIZED_PATH = Path("data/normalized/eea/stations/station_metadata.parquet")
OUT_DIR = Path("data/transformed/eea/stations")
OUT_DIR.mkdir(parents=True, exist_ok=True)
STATION_METADATA_PATH = OUT_DIR / "station_metadata.parquet"


def deduplicate_stations(df: pd.DataFrame) -> pd.DataFrame:
    before = len(df)
    df = df.drop_duplicates(subset=["AirQualityStationEoICode"])
    logger.info("Deduplicated stations | %s -> %s rows", before, len(df))
    return df


def run():
    logger.info("Starting EEA station metadata transformation")
    if not NORMALIZED_PATH.exists():
        raise FileNotFoundError(
            f"No normalized stations file at {NORMALIZED_PATH} — "
            f"run normalization.eea.stations first."
        )

    df = pd.read_parquet(NORMALIZED_PATH)
    df = deduplicate_stations(df)

    df.to_parquet(STATION_METADATA_PATH, index=False)
    logger.info("Transformed stations saved | path=%s rows=%s",
                STATION_METADATA_PATH.resolve(), len(df))


if __name__ == "__main__":
    run()
