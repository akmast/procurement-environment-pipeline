"""
Raw EEA measurements ingestion — test / historical / refresh modes.

One API request per pollutant, not one request covering all pollutants —
this way we already know which pollutant a file belongs to from the
request we made for it, and can lay raw storage out accordingly, instead
of relying on the numeric `Pollutant` code inside the file or merging by
that code later.

Saves parquet files exactly as received from the API, untouched. Country
scope is applied server-side via the `countries` field the API's own
request payload accepts — no other filtering, no date filtering beyond
the request window, no schema normalization, no dedup. All of that
belongs to the normalization layer.

Reads/writes go through common.storage (storage_mode="local" or "cloud",
see common/storage.py) — the download/request logic never branches on
storage_mode itself.

`mode="refresh"` re-checks the "mutable" years (current year, and the
previous year until its own reporting deadline — see
common/reporting_window.py). Each downloaded file is staged, validated
as a readable Parquet file, and only then hash-compared against what's
already stored (see common/staged_write.py) — a file is (re)written to
final storage only if it's both valid *and* changed. Years further in
the past are not part of this project's incremental refresh at all; see
docs/pipelines/eea_measurements.md for why.

    from ingestion.eea.measurements import run
    run(mode="test")
    run(mode="historical", from_year=2023, to_year=2025)
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
MANIFEST_PATH = f"{OUT_DIR}/manifest.jsonl"
STATE_PATH = f"{OUT_DIR}/state.json"
TEST_DIR = "data/raw/eea/test"

COUNTRY = "DE"  # server-side filter — the API's `countries` field accepts a list of codes
POLLUTANTS = ["PM10", "PM2.5", "NO2", "O3", "SO2"]  # one request per entry, not one request for all
DATASET = 1  # E2a / Unverified / UTD
AGGREGATION_TYPE = "day"


def validate_date(user_date: str) -> str:
    try:
        datetime.strptime(user_date, "%Y-%m-%d")
    except ValueError:
        raise ValueError(
            f"Invalid date {user_date!r} — expected YYYY-MM-DD, e.g. 2024-01-01."
        )
    return user_date


def build_request_body(from_date: str, to_date: str, pollutant: str) -> dict:
    return {
        "countries": [COUNTRY],
        "cities": [],
        "pollutants": [pollutant],
        "dataset": DATASET,
        "dateTimeStart": validate_date(from_date),
        "dateTimeEnd": validate_date(to_date),
        "aggregationType": AGGREGATION_TYPE,
        "source": "API",
    }


def get_file_urls(from_date: str, to_date: str, pollutant: str) -> list:
    payload = build_request_body(from_date, to_date, pollutant)
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

    logger.info("Requested file list | from=%s to=%s pollutant=%s -> %s files",
                from_date, to_date, pollutant, len(urls))
    return urls


def download_file(url: str) -> bytes:
    resp = request_with_retry("GET", url)
    return resp.content


def append_manifest_entry(entry: dict, storage_mode: str):
    append_text(MANIFEST_PATH, json.dumps(entry, ensure_ascii=False) + "\n", storage_mode)


def _download_and_save(url: str, dest_dir: str, i: int, total: int, year: int,
                        pollutant: str, state: dict, storage_mode: str) -> tuple[int, bool]:
    """Returns (bytes_downloaded, was_written) — was_written is False when
    validation failed or the content hash matched what's already stored,
    so nothing was (re)written to final storage."""
    pct = int(i / total * 100)
    filename = Path(url).name or f"file_{i}.parquet"
    dest_path = f"{dest_dir}/{filename}"
    logger.info("[%s/%s] %s%% — checking %s (pollutant=%s)", i, total, pct, filename, pollutant)

    content = download_file(url)
    written = stage_validate_and_write(
        dest_path, content, storage_mode, state, validate=is_valid_parquet
    )

    if not written:
        logger.info("Not written (unchanged or invalid) | %s", filename)
        return len(content), False

    append_manifest_entry({
        "url": url,
        "year": year,
        "pollutant": pollutant,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "size_bytes": len(content),
        "storage_path": dest_path,
    }, storage_mode)
    logger.info("Saved | %s (%.1f KB) -> %s", filename, len(content) / 1024, dest_path)
    return len(content), True


def download_pollutant_range(year: int, pollutant: str, from_date: str, to_date: str,
                              state: dict, storage_mode: str) -> dict:
    """Download every file for one pollutant within a date range, skipping
    unchanged ones. Returns {"files": total, "written": changed_count}."""
    pollutant_dir = f"{OUT_DIR}/{year}/{pollutant}"

    urls = get_file_urls(from_date, to_date, pollutant)
    written = 0
    for i, url in enumerate(urls, start=1):
        _, was_written = _download_and_save(
            url, pollutant_dir, i, len(urls), year, pollutant, state, storage_mode
        )
        written += int(was_written)

    logger.info("Pollutant finished | year=%s pollutant=%s files=%s written=%s",
                year, pollutant, len(urls), written)
    return {"files": len(urls), "written": written}


def download_year(year: int, state: dict, storage_mode: str) -> dict:
    results = [
        download_pollutant_range(year, pollutant, f"{year}-01-01", f"{year}-12-31", state, storage_mode)
        for pollutant in POLLUTANTS
    ]
    total_files = sum(r["files"] for r in results)
    total_written = sum(r["written"] for r in results)
    logger.info("Year finished | year=%s files=%s written=%s", year, total_files, total_written)
    return {"year": year, "files": total_files, "written": total_written}


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------

def run_test(storage_mode: str):
    """Small window, single pollutant. Doesn't touch real storage or state."""
    logger.info("Starting EEA measurements ingestion | mode=test storage_mode=%s", storage_mode)
    to_date = (datetime.now(timezone.utc) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    from_date = (datetime.now(timezone.utc) - pd.Timedelta(days=5)).strftime("%Y-%m-%d")

    pollutant = "PM10"
    urls = get_file_urls(from_date, to_date, pollutant)
    if not urls:
        logger.warning("Test request returned 0 files — check filters/date range")
        return

    url = urls[0]
    content = download_file(url)
    filename = Path(url).name or "test_ingestion.parquet"
    test_path = f"{TEST_DIR}/{filename}"
    write_bytes(test_path, content, storage_mode)

    df = pd.read_parquet(BytesIO(content))
    logger.info("Test file saved | path=%s size_kb=%.1f", test_path, len(content) / 1024)
    logger.info("Row count | %s", len(df))
    logger.info("Columns | %s", list(df.columns))
    if not df.empty:
        logger.info("Time range | start=%s end=%s", df["Start"].min(), df["End"].max())
        logger.info("Sample rows:\n%s", df.head(3).to_string())


def run_historical(from_year: int, to_year: int, storage_mode: str):
    if from_year > to_year:
        raise ValueError(f"from_year ({from_year}) is after to_year ({to_year})")

    logger.info("Starting EEA measurements ingestion | mode=historical years=%s-%s storage_mode=%s",
                from_year, to_year, storage_mode)
    state = load_state(STATE_PATH, storage_mode)
    results = [download_year(year, state, storage_mode) for year in range(from_year, to_year + 1)]
    save_state(STATE_PATH, state, storage_mode)

    total_files = sum(r["files"] for r in results)
    total_written = sum(r["written"] for r in results)
    logger.info("Historical ingestion finished | years=%s-%s total_files=%s total_written=%s",
                from_year, to_year, total_files, total_written)


def run_refresh(storage_mode: str):
    """
    Re-checks every year in the current reporting-mutable window (see
    common.reporting_window.mutable_years) — not a hardcoded year. Each
    file is re-requested and only rewritten if its content actually
    changed since the last run; unchanged files are left untouched.
    """
    years = mutable_years()
    logger.info("Starting EEA measurements ingestion | mode=refresh years=%s storage_mode=%s",
                years, storage_mode)

    state = load_state(STATE_PATH, storage_mode)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current_year = datetime.now(timezone.utc).year

    results = []
    for year in years:
        to_date = today if year == current_year else f"{year}-12-31"
        year_results = [
            download_pollutant_range(year, pollutant, f"{year}-01-01", to_date, state, storage_mode)
            for pollutant in POLLUTANTS
        ]
        total_files = sum(r["files"] for r in year_results)
        total_written = sum(r["written"] for r in year_results)
        logger.info("Year finished | year=%s files=%s written=%s", year, total_files, total_written)
        results.append({"year": year, "files": total_files, "written": total_written})

    save_state(STATE_PATH, state, storage_mode)

    total_files = sum(r["files"] for r in results)
    total_written = sum(r["written"] for r in results)
    logger.info("Refresh finished | years=%s total_files=%s total_written=%s",
                years, total_files, total_written)


def run(mode: str, storage_mode: str = "local",
        from_year: int | None = None, to_year: int | None = None):
    if mode == "test":
        run_test(storage_mode)
    elif mode == "historical":
        if from_year is None or to_year is None:
            raise ValueError("historical mode requires both from_year and to_year")
        run_historical(from_year, to_year, storage_mode)
    elif mode == "refresh":
        run_refresh(storage_mode)
    else:
        raise ValueError(
            f"Unknown mode {mode!r} — expected 'test', 'historical', or 'refresh'"
        )


if __name__ == "__main__":
    run(
        mode="test",           # Run mode: test, historical, or refresh
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
        from_year=None,        # Start year for historical mode, e.g. 2023
        to_year=None,          # End year for historical mode, e.g. 2025
    )
