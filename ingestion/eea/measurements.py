"""
Raw EEA measurements ingestion — test / historical / refresh modes.

One API request per pollutant, not one request covering all pollutants —
this way we already know which pollutant a file belongs to from the
request we made for it, and can lay raw storage out accordingly, instead
of relying on the numeric `Pollutant` code inside the file or merging by
that code later.

Also one request per country, per pollutant — mirrors the same reasoning:
the API's own `countries` field is a server-side filter (not a post-fetch
one), and requesting one country at a time lets raw storage be laid out
per country, with its own manifest/state, so ingesting a new country
never touches another country's staging/hash state.

Saves parquet files exactly as received from the API, untouched — no
filtering beyond country/date/pollutant, no schema normalization, no
dedup. All of that belongs to the normalization layer.

Reads/writes go through common.storage (storage_mode="local" or "cloud",
see common/storage.py) — the download/request logic never branches on
storage_mode itself.

`mode="refresh"` re-checks the "mutable" years (current year, and the
previous year until its own reporting deadline — see
common/reporting_window.py) for every requested country. Each downloaded
file is staged, validated as a readable Parquet file, and only then
hash-compared against what's already stored for that country (see
common/staged_write.py) — a file is (re)written to final storage only if
it's both valid *and* changed. Years further in the past are not part of
this project's incremental refresh at all; see
docs/pipelines/eea_measurements.md for why.

    from ingestion.eea.measurements import run
    run(mode="test")
    run(mode="historical", from_year=2023, to_year=2025)
    run(mode="historical", from_year=2023, to_year=2025, countries=["DE", "PL"])
    run(mode="refresh")
    run(mode="refresh", storage_mode="cloud")
"""
import json
import logging
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pandas as pd

# Make `common`/`ingestion` importable and logging configured regardless of
# how this file is run (python -m, import, Jupyter, or run directly).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.logging_config import setup_logging
from common.change_tracking import load_state, save_state
from common.reporting_window import mutable_years
from common.staged_write import stage_validate_and_write
from common.storage import append_text, write_bytes
from common.validation import is_valid_parquet

try:
    from .http_client import request_with_retry
except ImportError:
    from http_client import request_with_retry

setup_logging()
logger = logging.getLogger(__name__)

API_BASE = "https://eeadmz1-downloads-api-appservice.azurewebsites.net"
URLS_ENDPOINT = f"{API_BASE}/ParquetFile/urls"

OUT_DIR = "data/raw/eea/measurements"
TEST_DIR = "data/raw/eea/test"

DEFAULT_COUNTRIES = ["DE", "PL"]
POLLUTANTS = ["PM10", "PM2.5", "NO2", "O3", "SO2"]  # one request per entry, not one request for all
DATASET = 1  # E2a / Unverified / UTD
AGGREGATION_TYPE = "day"


def country_manifest_path(country: str) -> str:
    return f"{OUT_DIR}/{country}/manifest.jsonl"


def country_state_path(country: str) -> str:
    return f"{OUT_DIR}/{country}/state.json"


def validate_date(user_date: str) -> str:
    try:
        datetime.strptime(user_date, "%Y-%m-%d")
    except ValueError:
        raise ValueError(
            f"Invalid date {user_date!r} — expected YYYY-MM-DD, e.g. 2024-01-01."
        )
    return user_date


def build_request_body(from_date: str, to_date: str, pollutant: str, country: str) -> dict:
    return {
        "countries": [country],
        "cities": [],
        "pollutants": [pollutant],
        "dataset": DATASET,
        "dateTimeStart": validate_date(from_date),
        "dateTimeEnd": validate_date(to_date),
        "aggregationType": AGGREGATION_TYPE,
        "source": "API",
    }


def get_file_urls(from_date: str, to_date: str, pollutant: str, country: str) -> list:
    payload = build_request_body(from_date, to_date, pollutant, country)
    resp = request_with_retry("POST", URLS_ENDPOINT, json=payload)

    raw_text = resp.content.decode("utf-8-sig", errors="replace")
    try:
        data = json.loads(raw_text)
    except ValueError:
        data = raw_text

    if isinstance(data, list):
        urls = data
    elif isinstance(data, dict) and "urls" in data:
        urls = data["urls"]
    elif isinstance(data, str):
        lines = [line.strip() for line in data.splitlines() if line.strip()]
        urls = [line for line in lines if line.lower().startswith("http")]
    else:
        raise RuntimeError(f"Unexpected /ParquetFile/urls response shape: {type(data)}")

    logger.info("Requested file list | country=%s from=%s to=%s pollutant=%s -> %s files",
                country, from_date, to_date, pollutant, len(urls))
    return urls


def download_file(url: str) -> bytes:
    resp = request_with_retry("GET", url)
    return resp.content


def append_manifest_entry(entry: dict, country: str, storage_mode: str):
    append_text(country_manifest_path(country), json.dumps(entry, ensure_ascii=False) + "\n", storage_mode)


def _download_and_save(url: str, dest_dir: str, i: int, total: int, country: str, year: int,
                        pollutant: str, state: dict, storage_mode: str) -> tuple[int, bool, str | None]:
    """Returns (bytes_downloaded, was_written, dest_path). was_written is
    False when validation failed or the content hash matched what's
    already stored, so nothing was (re)written to final storage — in
    that case dest_path is None, since there's nothing new to hand to
    normalization/transformation."""
    pct = int(i / total * 100)
    filename = Path(url).name or f"file_{i}.parquet"
    dest_path = f"{dest_dir}/{filename}"
    logger.info("[%s/%s] %s%% — checking %s (country=%s pollutant=%s)", i, total, pct, filename, country, pollutant)

    content = download_file(url)
    written = stage_validate_and_write(
        dest_path, content, storage_mode, state, validate=is_valid_parquet
    )

    if not written:
        logger.info("Not written (unchanged or invalid) | %s", filename)
        return len(content), False, None

    append_manifest_entry({
        "url": url,
        "country": country,
        "year": year,
        "pollutant": pollutant,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "size_bytes": len(content),
        "storage_path": dest_path,
    }, country, storage_mode)
    logger.info("Saved | %s (%.1f KB) -> %s", filename, len(content) / 1024, dest_path)
    return len(content), True, dest_path


def download_pollutant_range(country: str, year: int, pollutant: str, from_date: str, to_date: str,
                              state: dict, storage_mode: str) -> dict:
    """Download every file for one country/pollutant within a date range,
    skipping unchanged ones. Returns {"files": total, "written": changed_count,
    "written_paths": [only the files actually (re)written this call]} —
    written_paths is what a caller passes straight into
    normalization/transformation to process only new data, instead of
    rescanning the whole country (see common.storage.resolve_paths)."""
    pollutant_dir = f"{OUT_DIR}/{country}/{year}/{pollutant}"

    urls = get_file_urls(from_date, to_date, pollutant, country)
    written_paths = []
    for i, url in enumerate(urls, start=1):
        _, was_written, dest_path = _download_and_save(
            url, pollutant_dir, i, len(urls), country, year, pollutant, state, storage_mode
        )
        if was_written:
            written_paths.append(dest_path)

    logger.info("Pollutant finished | country=%s year=%s pollutant=%s files=%s written=%s",
                country, year, pollutant, len(urls), len(written_paths))
    return {"files": len(urls), "written": len(written_paths), "written_paths": written_paths}


def download_year(country: str, year: int, state: dict, storage_mode: str) -> dict:
    results = [
        download_pollutant_range(country, year, pollutant, f"{year}-01-01", f"{year}-12-31", state, storage_mode)
        for pollutant in POLLUTANTS
    ]
    total_files = sum(r["files"] for r in results)
    written_paths = [path for r in results for path in r["written_paths"]]
    logger.info("Year finished | country=%s year=%s files=%s written=%s",
                country, year, total_files, len(written_paths))
    return {"year": year, "files": total_files, "written": len(written_paths), "written_paths": written_paths}


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------

def run_test(countries: list[str], storage_mode: str):
    """Small window, single pollutant, per country. Doesn't touch real storage or state."""
    logger.info("Starting EEA measurements ingestion | mode=test countries=%s storage_mode=%s",
                countries, storage_mode)
    to_date = (datetime.now(timezone.utc) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    from_date = (datetime.now(timezone.utc) - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    pollutant = "PM10"

    for country in countries:
        urls = get_file_urls(from_date, to_date, pollutant, country)
        if not urls:
            logger.warning("Test request returned 0 files | country=%s — check filters/date range", country)
            continue

        url = urls[0]
        content = download_file(url)
        filename = Path(url).name or "test_ingestion.parquet"
        test_path = f"{TEST_DIR}/{country}/{filename}"
        write_bytes(test_path, content, storage_mode)

        df = pd.read_parquet(BytesIO(content))
        logger.info("Test file saved | country=%s path=%s size_kb=%.1f", country, test_path, len(content) / 1024)
        logger.info("Row count | country=%s %s", country, len(df))
        logger.info("Columns | %s", list(df.columns))
        if not df.empty:
            logger.info("Time range | country=%s start=%s end=%s", country, df["Start"].min(), df["End"].max())
            logger.info("Sample rows:\n%s", df.head(3).to_string())


def run_historical(countries: list[str], from_year: int, to_year: int, storage_mode: str) -> dict:
    """Returns {country: {"files": ..., "written": ..., "written_paths": [...]}}
    — written_paths is every file actually (re)written across all requested
    years for that country, ready to hand to normalization/transformation
    (see common.storage.resolve_paths) instead of reprocessing everything."""
    if from_year > to_year:
        raise ValueError(f"from_year ({from_year}) is after to_year ({to_year})")

    logger.info("Starting EEA measurements ingestion | mode=historical countries=%s years=%s-%s storage_mode=%s",
                countries, from_year, to_year, storage_mode)

    summary = {}
    for country in countries:
        state = load_state(country_state_path(country), storage_mode)
        results = [download_year(country, year, state, storage_mode) for year in range(from_year, to_year + 1)]
        save_state(country_state_path(country), state, storage_mode)

        total_files = sum(r["files"] for r in results)
        written_paths = [path for r in results for path in r["written_paths"]]
        logger.info("Historical ingestion finished | country=%s years=%s-%s total_files=%s total_written=%s",
                    country, from_year, to_year, total_files, len(written_paths))
        summary[country] = {"files": total_files, "written": len(written_paths), "written_paths": written_paths}

    return summary


def run_refresh(countries: list[str], storage_mode: str) -> dict:
    """
    Re-checks every year in the current reporting-mutable window (see
    common.reporting_window.mutable_years) for each requested country —
    not a hardcoded year. Each file is re-requested and only rewritten if
    its content actually changed since the last run; unchanged files are
    left untouched.

    Returns {country: {"files": ..., "written": ..., "written_paths": [...]}}
    — written_paths is every file that actually changed this run, the
    piece a refresh workflow needs to run normalization/transformation
    on only the new data instead of the whole country (see
    common.storage.resolve_paths).
    """
    years = mutable_years()
    logger.info("Starting EEA measurements ingestion | mode=refresh countries=%s years=%s storage_mode=%s",
                countries, years, storage_mode)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current_year = datetime.now(timezone.utc).year

    summary = {}
    for country in countries:
        state = load_state(country_state_path(country), storage_mode)

        results = []
        for year in years:
            to_date = today if year == current_year else f"{year}-12-31"
            year_results = [
                download_pollutant_range(country, year, pollutant, f"{year}-01-01", to_date, state, storage_mode)
                for pollutant in POLLUTANTS
            ]
            total_files = sum(r["files"] for r in year_results)
            written_paths = [path for r in year_results for path in r["written_paths"]]
            logger.info("Year finished | country=%s year=%s files=%s written=%s",
                        country, year, total_files, len(written_paths))
            results.append({"year": year, "files": total_files, "written_paths": written_paths})

        save_state(country_state_path(country), state, storage_mode)

        total_files = sum(r["files"] for r in results)
        written_paths = [path for r in results for path in r["written_paths"]]
        logger.info("Refresh finished | country=%s years=%s total_files=%s total_written=%s",
                    country, years, total_files, len(written_paths))
        summary[country] = {"files": total_files, "written": len(written_paths), "written_paths": written_paths}

    return summary


def run(mode: str, storage_mode: str = "local", countries: list[str] | None = None,
        from_year: int | None = None, to_year: int | None = None):
    """
    historical/refresh modes return {country: {"files", "written",
    "written_paths"}} — pass written_paths straight into normalization/
    transformation to process only the data that actually changed:

        written = run(mode="refresh")
        all_new_files = [p for country in written.values() for p in country["written_paths"]]
        if all_new_files:
            normalization.eea.measurements.run(countries=all_new_files)
    """
    countries = countries or DEFAULT_COUNTRIES
    if mode == "test":
        run_test(countries, storage_mode)
        return None
    elif mode == "historical":
        if from_year is None or to_year is None:
            raise ValueError("historical mode requires both from_year and to_year")
        return run_historical(countries, from_year, to_year, storage_mode)
    elif mode == "refresh":
        return run_refresh(countries, storage_mode)
    else:
        raise ValueError(
            f"Unknown mode {mode!r} — expected 'test', 'historical', or 'refresh'"
        )


if __name__ == "__main__":
    run(
        mode="test",             # Run mode: test, historical, or refresh
        storage_mode="local",    # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
        countries=["DE"],        # e.g. ["DE", "PL"] — one API request per country per pollutant
        from_year=None,          # Start year for historical mode, e.g. 2023
        to_year=None,            # End year for historical mode, e.g. 2025
    )
