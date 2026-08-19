"""
Raw EEA measurements ingestion — test / historical / refresh_current.

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

    from ingestion.eea.measurements import run
    run(mode="test")
    run(mode="historical", from_year=2023, to_year=2025)
    run(mode="refresh_current")
"""
import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# Make `common`/`ingestion` importable and logging configured regardless of
# how this file is run (python -m, import, Jupyter, or run directly).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.logging_config import setup_logging

try:
    from .http_client import request_with_retry
except ImportError:
    from http_client import request_with_retry

setup_logging()
logger = logging.getLogger(__name__)

API_BASE = "https://eeadmz1-downloads-api-appservice.azurewebsites.net"
URLS_ENDPOINT = f"{API_BASE}/ParquetFile/urls"

OUT_DIR = Path("data/raw/eea/measurements")
MANIFEST_PATH = OUT_DIR / "manifest.jsonl"
TEST_DIR = Path("data/raw/eea/test")
TEST_DIR.mkdir(parents=True, exist_ok=True)

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


def append_manifest_entry(entry: dict):
    with open(MANIFEST_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def remove_manifest_entries_for_year(year: int):
    if not MANIFEST_PATH.exists():
        return
    kept = []
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("year") != year:
                kept.append(entry)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        for entry in kept:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _download_and_save(url: str, dest_dir: Path, i: int, total: int, year: int, pollutant: str):
    pct = int(i / total * 100)
    filename = Path(url).name or f"file_{i}.parquet"
    logger.info("[%s/%s] %s%% — downloading %s (pollutant=%s)", i, total, pct, filename, pollutant)

    content = download_file(url)
    local_path = dest_dir / filename
    local_path.write_bytes(content)

    append_manifest_entry({
        "url": url,
        "year": year,
        "pollutant": pollutant,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "size_bytes": len(content),
        "local_path": str(local_path),
    })
    logger.info("Saved | %s (%.1f KB) -> %s", filename, len(content) / 1024, local_path)
    return len(content)


def download_pollutant_range(year: int, pollutant: str, from_date: str, to_date: str) -> int:
    """Download every file for one pollutant within a date range. Returns file count."""
    pollutant_dir = OUT_DIR / str(year) / pollutant
    pollutant_dir.mkdir(parents=True, exist_ok=True)

    urls = get_file_urls(from_date, to_date, pollutant)
    for i, url in enumerate(urls, start=1):
        _download_and_save(url, pollutant_dir, i, len(urls), year, pollutant)

    logger.info("Pollutant finished | year=%s pollutant=%s files=%s", year, pollutant, len(urls))
    return len(urls)


def download_year(year: int) -> dict:
    total_files = sum(
        download_pollutant_range(year, pollutant, f"{year}-01-01", f"{year}-12-31")
        for pollutant in POLLUTANTS
    )
    logger.info("Year finished | year=%s files=%s", year, total_files)
    return {"year": year, "files": total_files}


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------

def run_test():
    """Small window, single pollutant. Doesn't touch real storage."""
    logger.info("Starting EEA measurements ingestion | mode=test")
    to_date = (datetime.now(timezone.utc) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    from_date = (datetime.now(timezone.utc) - pd.Timedelta(days=5)).strftime("%Y-%m-%d")

    pollutant = "PM10"
    urls = get_file_urls(from_date, to_date, pollutant)
    if not urls:
        logger.warning("Test request returned 0 files — check filters/date range")
        return

    url = urls[0]
    content = download_file(url)
    test_path = TEST_DIR / (Path(url).name or "test_ingestion.parquet")
    test_path.write_bytes(content)

    df = pd.read_parquet(test_path)
    logger.info("Test file saved | path=%s size_kb=%.1f", test_path.resolve(), len(content) / 1024)
    logger.info("Row count | %s", len(df))
    logger.info("Columns | %s", list(df.columns))
    if not df.empty:
        logger.info("Time range | start=%s end=%s", df["Start"].min(), df["End"].max())
        logger.info("Sample rows:\n%s", df.head(3).to_string())


def run_historical(from_year: int, to_year: int):
    if from_year > to_year:
        raise ValueError(f"from_year ({from_year}) is after to_year ({to_year})")

    logger.info("Starting EEA measurements ingestion | mode=historical years=%s-%s",
                from_year, to_year)
    results = [download_year(year) for year in range(from_year, to_year + 1)]
    total_files = sum(r["files"] for r in results)
    logger.info("Historical ingestion finished | years=%s-%s total_files=%s",
                from_year, to_year, total_files)


def run_refresh_current():
    year = datetime.now(timezone.utc).year
    logger.info("Starting EEA measurements ingestion | mode=refresh_current year=%s", year)

    year_dir = OUT_DIR / str(year)
    if year_dir.exists():
        shutil.rmtree(year_dir)
        logger.info("Deleted existing files for year=%s", year)
    remove_manifest_entries_for_year(year)

    to_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total_files = sum(
        download_pollutant_range(year, pollutant, f"{year}-01-01", to_date)
        for pollutant in POLLUTANTS
    )
    logger.info("Refresh finished | year=%s files=%s", year, total_files)


def run(mode: str, from_year: int | None = None, to_year: int | None = None):
    if mode == "test":
        run_test()
    elif mode == "historical":
        if from_year is None or to_year is None:
            raise ValueError("historical mode requires both from_year and to_year")
        run_historical(from_year, to_year)
    elif mode == "refresh_current":
        run_refresh_current()
    else:
        raise ValueError(
            f"Unknown mode {mode!r} — expected 'test', 'historical', or 'refresh_current'"
        )


if __name__ == "__main__":
    run(
        mode="test",  # Run mode: test, historical, or refresh_current
        from_year=None,  # Start year for historical mode, e.g. 2023
        to_year=None,  # End year for historical mode, e.g. 2025
    )
