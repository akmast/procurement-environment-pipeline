"""
EEA station metadata transformation — deduplication + NUTS enrichment.

Reads the normalized (flattened, cleaned) station table produced by
normalization.eea.stations — one file per country,
data/normalized/eea/stations/<country>/station_metadata.parquet —
deduplicates each by station code, and adds nuts1_code/nuts2_code/
nuts3_code derived from each station's (longitude, latitude) via
point-in-polygon matching against the official NUTS3 boundaries fetched
by ingestion.eea.nuts_boundaries. Deduplication and NUTS enrichment are
both heavier, opinionated decisions (which rows are "the same station";
which region a coordinate falls in) — that's why they live here rather
than in normalization.

The NUTS3 boundaries themselves are EU-wide reference data, not scoped
to any one country — they're loaded once per run and reused for every
country's stations, not reloaded per country.

If `countries` isn't passed, every country already normalized (i.e.
every subdirectory under data/normalized/eea/stations/) is processed —
read from the normalized layer's own directory structure, not guessed.

NUTS1/NUTS2 are not spatially matched separately: NUTS codes are
hierarchical by construction (a NUTS3 code's first 3/4 characters *are*
its NUTS1/NUTS2 code, e.g. "DE712" -> NUTS2 "DE71" -> NUTS1 "DE7"), so
matching against NUTS3 polygons alone is enough to derive all three
levels. See docs/pipelines/eea_nuts_boundaries.md for where the boundary
data comes from and how the spatial join works.

A station with a missing coordinate, or a coordinate that doesn't fall
inside any known NUTS3 polygon (e.g. just outside a coastline), gets
nuts1_code/nuts2_code/nuts3_code = None rather than failing the run.

Reads/writes go through common.storage, so storage_mode="local" (default)
and storage_mode="cloud" (S3) run the same logic.

    from transformation.eea.stations import run
    run()
    run(countries=["DE", "PL"])
    run(storage_mode="cloud")

Requires: pip install shapely
"""
import json
import logging
import sys
from io import BytesIO
from pathlib import Path

import pandas as pd
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.storage import exists, list_files, read_bytes, write_bytes

logger = logging.getLogger(__name__)

NORMALIZED_BASE_DIR = "data/normalized/eea/stations"
TRANSFORMED_BASE_DIR = "data/transformed/eea/stations"
STATION_METADATA_FILENAME = "station_metadata.parquet"
NUTS_BOUNDARIES_PATH = "data/reference/eea/nuts_boundaries/nuts3_boundaries.geojson"

# GISCO's own property key for the region code on each NUTS boundary feature.
NUTS_ID_PROPERTY = "NUTS_ID"


def deduplicate_stations(df: pd.DataFrame) -> pd.DataFrame:
    before = len(df)
    df = df.drop_duplicates(subset=["AirQualityStationEoICode"])
    logger.info("Deduplicated stations | %s -> %s rows", before, len(df))
    return df


def load_nuts3_geometries(storage_mode: str) -> tuple[list, list[str]] | None:
    """
    Returns (geometries, nuts3_codes) — parallel lists, same order — or
    None if the boundaries reference file isn't available. Missing
    boundaries is treated as a soft failure: enrichment is skipped and
    every station gets empty NUTS fields, the pipeline doesn't crash.
    """
    if not exists(NUTS_BOUNDARIES_PATH, storage_mode):
        logger.warning(
            "NUTS boundaries file not found at %s — run "
            "ingestion.eea.nuts_boundaries first; NUTS fields will be left empty",
            NUTS_BOUNDARIES_PATH,
        )
        return None

    geojson = json.loads(read_bytes(NUTS_BOUNDARIES_PATH, storage_mode).decode("utf-8"))

    geometries = []
    codes = []
    skipped = 0
    for feature in geojson.get("features", []):
        properties = feature.get("properties") or {}
        code = properties.get(NUTS_ID_PROPERTY)
        geometry = feature.get("geometry")
        if not code or not geometry:
            skipped += 1
            continue
        try:
            geometries.append(shape(geometry))
            codes.append(code)
        except Exception as exc:
            logger.warning("Skipped unparseable NUTS3 geometry | code=%s error=%s", code, exc)
            skipped += 1

    logger.info("Loaded NUTS3 boundaries | polygons=%s skipped=%s", len(codes), skipped)
    return geometries, codes


def match_nuts3_codes(df: pd.DataFrame, geometries: list, codes: list[str]) -> list[str | None]:
    """
    For each station, finds the NUTS3 polygon containing its
    (longitude, latitude) point. STRtree.query() is a fast bounding-box
    pre-filter — it returns candidate polygon indices whose *envelope*
    overlaps the point, not confirmed matches, so each candidate is still
    checked with an exact .contains() test before being accepted.
    """
    tree = STRtree(geometries)
    matches: list[str | None] = []
    unmatched = 0

    for longitude, latitude in zip(df["longitude"], df["latitude"]):
        if pd.isna(longitude) or pd.isna(latitude):
            matches.append(None)
            unmatched += 1
            continue

        point = Point(longitude, latitude)
        match = None
        for idx in tree.query(point):
            if geometries[idx].contains(point):
                match = codes[idx]
                break

        if match is None:
            unmatched += 1
        matches.append(match)

    logger.info("NUTS3 matching finished | matched=%s unmatched=%s",
                len(matches) - unmatched, unmatched)
    return matches


def enrich_with_nuts(df: pd.DataFrame, boundaries: tuple[list, list[str]] | None) -> pd.DataFrame:
    if boundaries is None:
        df["nuts1_code"] = None
        df["nuts2_code"] = None
        df["nuts3_code"] = None
        return df

    geometries, codes = boundaries
    nuts3_codes = match_nuts3_codes(df, geometries, codes)

    df["nuts3_code"] = nuts3_codes
    # NUTS codes nest by prefix: NUTS3 "DE712" -> NUTS2 "DE71" -> NUTS1 "DE7".
    df["nuts2_code"] = [code[:4] if code else None for code in nuts3_codes]
    df["nuts1_code"] = [code[:3] if code else None for code in nuts3_codes]
    return df


def discover_countries(storage_mode: str) -> list[str]:
    """Country codes come from the normalized layer's own <country>/ subdirectories."""
    normalized_files = list_files(NORMALIZED_BASE_DIR, storage_mode, suffix=STATION_METADATA_FILENAME)
    return sorted({Path(p).parent.name for p in normalized_files})


def run(storage_mode: str = "local", countries: list[str] | None = None):
    countries = countries or discover_countries(storage_mode)
    if not countries:
        logger.warning("No normalized station files found under %s", NORMALIZED_BASE_DIR)
        return

    logger.info("Starting EEA station metadata transformation | countries=%s storage_mode=%s",
                countries, storage_mode)

    # NUTS boundaries are EU-wide reference data, not per-country — loaded
    # once and reused for every country's stations below.
    boundaries = load_nuts3_geometries(storage_mode)

    for country in countries:
        normalized_path = f"{NORMALIZED_BASE_DIR}/{country}/{STATION_METADATA_FILENAME}"
        if not exists(normalized_path, storage_mode):
            raise FileNotFoundError(
                f"No normalized stations file at {normalized_path} — "
                f"run normalization.eea.stations first."
            )

        df = pd.read_parquet(BytesIO(read_bytes(normalized_path, storage_mode)))
        df = deduplicate_stations(df)
        df = enrich_with_nuts(df, boundaries)

        out_path = f"{TRANSFORMED_BASE_DIR}/{country}/{STATION_METADATA_FILENAME}"
        buffer = BytesIO()
        df.to_parquet(buffer, index=False)
        write_bytes(out_path, buffer.getvalue(), storage_mode)

        logger.info("Transformed stations saved | country=%s path=%s rows=%s",
                    country, out_path, len(df))


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
        countries=["DE"],      # e.g. ["DE", "PL"] — omit/None to auto-discover from data/normalized/eea/stations/
    )
