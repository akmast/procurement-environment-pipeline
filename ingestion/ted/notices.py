"""
TED API v3 ingestion module — test / historical / incremental modes.

Not a standalone entry point — called from root main.py:

    from ingestion.ted.notices import run as run_ted_ingestion
    run_ted_ingestion(mode="test")
    run_ted_ingestion(mode="historical", from_date="2025-01-01", to_date="2025-01-31")
    run_ted_ingestion(mode="incremental")
    run_ted_ingestion(mode="incremental", countries=["DE", "PL"])
    run_ted_ingestion(mode="incremental", storage_mode="cloud")

Saves notices exactly as TED returns them for the requested FIELDS — no
field stripping, no language trimming, no added columns. That JSON
reshaping (including stamping an explicit country_code) happens in
normalization.ted.notices. Country scope (buyer-country) is applied
server-side, inside the TED query string itself — TED's expert query
language processes it before any notices are returned, so this is not a
post-fetch filter.

Multiple countries: one TED query per country (mirrors the same
one-request-per-scope pattern used by the EEA pipelines), each with its
own storage path, dedup set, and state.json cursor — a slow/failed
country never blocks or corrupts another's progress. The project's
`countries` parameter is ISO2 (e.g. "DE", "PL", matching every other
source and the storage directory names) — TED's own query language wants
ISO3 (`buyer-country=DEU`), so ISO2 is translated to ISO3 only for
building the query string via EU_ISO2_TO_ISO3 below; storage paths and
the country_code column added in normalization stay ISO2.

The publication-number dedup here is a storage/idempotency concern, not a
data-cleaning one: it stops repeated incremental runs from re-appending
notices already on disk. It stays in ingestion for that reason — see the
project docs for more on this distinction. Dedup is scoped per country
(each country has its own notices.jsonl), so it never needs cross-country
publication-number comparisons.

Reads/writes go through common.storage (storage_mode="local" or "cloud",
see common/storage.py). Unlike EEA measurements, this source has no
content-hash change detection and no reporting-window refresh logic —
TED notices are treated as immutable once published, and publication-
number dedup already gives correct incremental behavior; there's no
redownloadable "whole snapshot" file here to hash.

Confirmed via a live test call (2026-08-19, 3-notice request, SORT BY +
paginationMode=ITERATION):
  - Top-level response keys are `notices`, `totalNoticeCount`,
    `iterationNextToken`, `timedOut` — `results` was never actually used
    by the API, the `.get("notices", data.get("results", []))` fallback
    below is now just defensive.
  - SORT BY works together with paginationMode=ITERATION — a valid
    iterationNextToken came back.
  - `timedOut` is a field we didn't know about before — see
    paginate_iteration(), a True value there means TED's backend gave up
    before finishing the search, so that batch may be incomplete.

Still unconfirmed — needs a real historical/incremental run to check,
since it depends on data volume, not response shape:
  - Whether publication-number is a safe, collision-free dedup key
    (historical/incremental report duplicate counts so this is
    checkable; corrigenda/revisions might publish under a new number).

Requires: pip install requests
"""
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# Make `common` importable and logging configured regardless of how this
# file is run (python -m, import, Jupyter, or run directly).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.logging_config import setup_logging
from common.storage import append_text, exists, read_text, write_text

setup_logging()
logger = logging.getLogger(__name__)

BASE_URL = "https://api.ted.europa.eu/v3/notices/search"

OUT_DIR = "data/raw/ted"

DEFAULT_COUNTRIES = ["DE"]  # preserves the pipeline's previous single-country (ISO2) behavior

# TED's query language wants ISO3 buyer-country codes; the rest of this
# project (storage paths, the countries= parameter, other sources) uses
# ISO2. Scoped to EU member states, matching this pipeline's actual scope
# (EU public procurement) — an unmapped code fails loudly (see
# iso2_to_iso3 below) rather than silently guessing a conversion.
EU_ISO2_TO_ISO3 = {
    "AT": "AUT", "BE": "BEL", "BG": "BGR", "HR": "HRV", "CY": "CYP",
    "CZ": "CZE", "DK": "DNK", "EE": "EST", "FI": "FIN", "FR": "FRA",
    "DE": "DEU", "GR": "GRC", "HU": "HUN", "IE": "IRL", "IT": "ITA",
    "LV": "LVA", "LT": "LTU", "LU": "LUX", "MT": "MLT", "NL": "NLD",
    "PL": "POL", "PT": "PRT", "RO": "ROU", "SK": "SVK", "SI": "SVN",
    "ES": "ESP", "SE": "SWE",
}

FIELDS = [
    "publication-number", "notice-title", "buyer-name", "buyer-country",
    "buyer-city", "buyer-post-code",
    "place-of-performance", "BT-5071-Lot", "classification-cpv",
    "total-value", "total-value-cur", "publication-date", "notice-type",
    "winner-name", "winner-selection-status", "contract-conclusion-date",
    "non-award-justification", "green-procurement-criteria-lot",
]

# Environment-related CPV codes (division-level)
ENV_CPV_CODES = [
    "90000000", "71313000", "09310000", "09330000", "09332000", "77000000",
]

SORT_BY_CLAUSE = "SORT BY publication-date DESC"

MAX_RETRIES = 3


def iso2_to_iso3(country: str) -> str:
    try:
        return EU_ISO2_TO_ISO3[country]
    except KeyError:
        raise ValueError(
            f"Unknown country {country!r} — expected an EU member state ISO2 "
            f"code (e.g. 'DE', 'PL'). Known codes: {sorted(EU_ISO2_TO_ISO3)}"
        )


def country_paths(country: str) -> dict:
    base = f"{OUT_DIR}/{country}"
    return {
        "test": f"{base}/test_ingestion.json",
        "dataset": f"{base}/notices.jsonl",
        "state": f"{base}/state.json",
    }


def to_ted_date(user_date: str) -> str:
    """
    Convert user-facing YYYY-MM-DD into the plain YYYYMMDD format TED's
    query date operators expect (no dashes/time/timezone — confirmed via
    TED Help, `date` is a "numerical search field").

    Not to be confused with the date *values* TED returns inside notice
    JSON (e.g. "2026-08-07+02:00") — those are left untouched.
    """
    try:
        datetime.strptime(user_date, "%Y-%m-%d")
    except ValueError:
        raise ValueError(
            f"Invalid date {user_date!r} — expected YYYY-MM-DD, e.g. "
            f"2025-01-01 (no time, no timezone)."
        )
    return user_date.replace("-", "")


# --------------------------------------------------------------------------
# Query construction — parameterized, no hardcoded date
# --------------------------------------------------------------------------

def build_query(iso3: str, from_date: str = None, to_date: str = None,
                 since_date: str = None, sort: bool = True) -> str:
    """
    Base filters (buyer-country / notice-type / environment CPV) are fixed
    and all evaluated server-side by TED. Only the date condition changes:
      - since_date  -> incremental: publication-date>=<last run>
      - from/to     -> historical, bounded range
      - from only   -> historical, open-ended
      - none        -> full available history

    We filter on `publication-date` (when the notice appeared), not
    `contract-conclusion-date` (when the contract was signed — that's data
    about the procurement itself, untouched by ingestion). Caveat:
    publication-date is first-publication date, not necessarily "last
    updated" — corrigenda/revisions may not bump it enough for the
    >=last_run filter to catch them. Not yet acted on.
    """
    cpv_clause = " OR ".join(f"classification-cpv={c}" for c in ENV_CPV_CODES)
    parts = [
        f"buyer-country={iso3}",
        "notice-type=can-standard",
        f"({cpv_clause})",
    ]

    if since_date:
        parts.append(f"publication-date>={to_ted_date(since_date)}")
    elif from_date and to_date:
        if from_date > to_date:  # YYYY-MM-DD sorts lexically, safe as string compare
            raise ValueError(
                f"--from-date ({from_date}) is after --to-date ({to_date}) — "
                f"swap them, the range is empty/inverted."
            )
        parts.append(f"publication-date>={to_ted_date(from_date)}")
        parts.append(f"publication-date<={to_ted_date(to_date)}")
    elif from_date:
        parts.append(f"publication-date>={to_ted_date(from_date)}")

    query = " AND ".join(parts)
    if sort:
        query += f" {SORT_BY_CLAUSE}"
    return query


# --------------------------------------------------------------------------
# API layer
# --------------------------------------------------------------------------

def call_api(payload: dict) -> dict:
    """
    One POST call with limited retries on transient errors (429/503/
    network), respecting Retry-After when present. Non-transient errors
    bubble up immediately.
    """
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(BASE_URL, json=payload, timeout=30)
        except requests.RequestException as exc:
            last_exc = exc
            wait = 2 ** attempt
            logger.warning(
                "API network error, retrying | attempt=%s/%s wait=%ss error=%s",
                attempt, MAX_RETRIES, wait, exc,
            )
            time.sleep(wait)
            continue

        if resp.status_code in (429, 503):
            retry_after = int(resp.headers.get("retry-after", 2 ** attempt))
            logger.warning(
                "API rate limited, waiting | status=%s attempt=%s/%s wait=%ss",
                resp.status_code, attempt, MAX_RETRIES, retry_after,
            )
            time.sleep(retry_after)
            continue

        if not resp.ok:
            # Body not logged at INFO/ERROR — can be large, DEBUG preview only
            logger.error("TED API request failed | status=%s", resp.status_code)
            logger.debug("Failed request query: %s", payload.get("query"))
            logger.debug("Response body preview: %s", resp.text[:300])
            resp.raise_for_status()

        return resp.json()

    raise RuntimeError(
        f"API call failed after {MAX_RETRIES} retries: {last_exc}"
    )


# --------------------------------------------------------------------------
# Pagination
# --------------------------------------------------------------------------

def paginate_iteration(query: str):
    """
    Generator: yields one batch at a time using paginationMode=ITERATION,
    without holding the full dataset in memory.

    Stops on: totalNoticeCount == 0 (nothing to fetch), an empty batch
    (TED has been seen to keep returning a token after results run out —
    this is a safety net against looping forever), or a missing
    iterationNextToken (normal end-of-results).
    """
    token = None
    request_count = 0

    while True:
        payload = {
            "query": query,
            "fields": FIELDS,
            "limit": 250,
            "scope": "ALL",
            "paginationMode": "ITERATION",
            "onlyLatestVersions": True,
        }
        if token:
            payload["iterationNextToken"] = token

        request_count += 1
        data = call_api(payload)

        if data.get("timedOut"):
            logger.warning(
                "TED reported timedOut=true | request=%s — this batch may be "
                "incomplete, TED's backend gave up before finishing the search",
                request_count,
            )

        if request_count == 1:
            logger.debug("Response top-level keys: %s", list(data.keys()))
            total = data.get("totalNoticeCount")
            if total is not None:
                logger.info("Total notices matching query | total=%s", total)
                if total == 0:
                    logger.warning(
                        "0 total notices for this query — stopping. "
                        "(Check date range — from-date must be <= to-date.)"
                    )
                    return

        notices = data.get("notices", data.get("results", []))
        logger.info("Notices received | request=%s count=%s",
                    request_count, len(notices))

        if not notices:
            logger.warning(
                "Received an empty batch — stopping (treated as "
                "end-of-results even if a token is still present)"
            )
            return

        yield notices

        token = data.get("iterationNextToken")
        if not token:
            logger.info("No iterationNextToken in response — pagination finished")
            break
        else:
            logger.debug("iterationNextToken received, continuing to next page")


# --------------------------------------------------------------------------
# Storage — isolated so it can later be swapped for a real DB
# --------------------------------------------------------------------------

def load_existing_publication_numbers(path: str, storage_mode: str) -> set:
    seen = set()
    if not exists(path, storage_mode):
        return seen
    for line in read_text(path, storage_mode).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            seen.add(json.loads(line).get("publication-number"))
        except json.JSONDecodeError:
            continue
    return seen


def append_batch_jsonl(path: str, batch: list, seen: set, storage_mode: str) -> tuple:
    """Append new notices to JSONL, skipping existing publication-numbers."""
    new_count = 0
    dup_count = 0
    lines = []
    for notice in batch:
        pub_num = notice.get("publication-number")
        if pub_num in seen:
            dup_count += 1
            continue
        lines.append(json.dumps(notice, ensure_ascii=False))
        seen.add(pub_num)
        new_count += 1
    if lines:
        append_text(path, "\n".join(lines) + "\n", storage_mode)
    return new_count, dup_count


def load_state(state_path: str, storage_mode: str) -> dict:
    if exists(state_path, storage_mode):
        return json.loads(read_text(state_path, storage_mode))
    return {}


def save_state(state_path: str, state: dict, storage_mode: str):
    write_text(state_path, json.dumps(state, ensure_ascii=False, indent=2), storage_mode)


def update_state_if_newer(state_path: str, new_date: str, storage_mode: str) -> bool:
    """
    Set last_successful_run_date = new_date, unless state.json already has
    a date that's the same or later (never move the cursor backwards —
    e.g. a historical backfill for an old period shouldn't undo progress
    already made by incremental runs). Returns True if state was updated.
    """
    state = load_state(state_path, storage_mode)
    current = state.get("last_successful_run_date")
    if current and current >= new_date:
        logger.info(
            "State not updated — existing last_successful_run_date=%s is "
            "not earlier than %s", current, new_date,
        )
        return False
    state["last_successful_run_date"] = new_date
    save_state(state_path, state, storage_mode)
    return True


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------

def run_test(countries: list[str], storage_mode: str):
    """One record per country, PAGE_NUMBER mode. Does not touch state.json or the dataset."""
    logger.info("Starting TED ingestion | mode=test countries=%s storage_mode=%s", countries, storage_mode)

    for country in countries:
        iso3 = iso2_to_iso3(country)
        paths = country_paths(country)
        payload = {
            "query": build_query(iso3, sort=True),
            "fields": FIELDS,
            "page": 1,
            "limit": 1,
            "paginationMode": "PAGE_NUMBER",
        }
        data = call_api(payload)
        logger.info("API request completed | country=%s status=200", country)
        logger.debug("Response top-level keys: %s", list(data.keys()))

        notices = data.get("notices", data.get("results", []))
        if not notices:
            logger.warning("Test request returned 0 notices | country=%s — check the query filters", country)
            continue

        logger.info("Notices received | country=%s count=%s", country, len(notices))

        write_text(paths["test"], json.dumps(notices, ensure_ascii=False, indent=2), storage_mode)
        logger.info("Test ingestion saved | country=%s path=%s", country, paths["test"])

        # Summary at INFO; full record only at DEBUG (it's a sizeable blob)
        first = notices[0]
        logger.info(
            "Sample notice | country=%s publication_number=%s notice_type=%s publication_date=%s",
            country, first.get("publication-number"), first.get("notice-type"),
            first.get("publication-date"),
        )
        logger.debug("Full sample notice: %s", json.dumps(first, ensure_ascii=False))


def run_historical(countries: list[str], storage_mode: str, from_date: str = None, to_date: str = None):
    """Full pagination via ITERATION mode per country, saved batch-by-batch as JSONL."""
    logger.info("Starting TED ingestion | mode=historical countries=%s from_date=%s to_date=%s storage_mode=%s",
                countries, from_date, to_date, storage_mode)

    for country in countries:
        iso3 = iso2_to_iso3(country)
        paths = country_paths(country)
        start_time = time.monotonic()

        total_received = 0
        total_new = 0
        total_dup = 0
        batch_count = 0

        try:
            query = build_query(iso3, from_date=from_date, to_date=to_date, sort=True)
            logger.debug("Historical query | country=%s: %s", country, query)

            seen = load_existing_publication_numbers(paths["dataset"], storage_mode)
            logger.info("Existing publication-numbers on disk | country=%s count=%s", country, len(seen))

            for batch in paginate_iteration(query):
                batch_count += 1
                total_received += len(batch)
                new_count, dup_count = append_batch_jsonl(paths["dataset"], batch, seen, storage_mode)
                total_new += new_count
                total_dup += dup_count
                logger.info(
                    "Batch saved | country=%s batch=%s new=%s duplicates=%s running_total=%s",
                    country, batch_count, new_count, dup_count, len(seen),
                )
        except Exception:
            logger.exception("Historical ingestion failed | country=%s — state.json will NOT be updated", country)
            raise

        elapsed = time.monotonic() - start_time

        logger.info("Historical ingestion finished | country=%s", country)
        logger.info(
            "Historical ingestion summary | country=%s requests=%s received=%s "
            "unique_on_disk=%s duplicates=%s new_saved=%s elapsed=%.1fs output_path=%s",
            country, batch_count, total_received, len(seen), total_dup, total_new,
            elapsed, paths["dataset"],
        )

        # Only a bounded range (explicit to_date) represents a fully-covered
        # period we can safely record as "successfully loaded through". An
        # open-ended historical load (no to_date) has no such endpoint, so we
        # deliberately don't touch state.json in that case.
        if to_date:
            updated = update_state_if_newer(paths["state"], to_date, storage_mode)
            if updated:
                logger.info(
                    "Historical load completed successfully. State updated | "
                    "country=%s last_successful_date=%s", country, to_date,
                )
            else:
                logger.info(
                    "Historical load completed successfully. State left "
                    "unchanged | country=%s (see reason above).", country,
                )
        else:
            logger.info(
                "Historical load completed successfully. No --to-date given — "
                "state.json left unchanged | country=%s", country,
            )


def run_incremental(countries: list[str], storage_mode: str):
    """Loads notices since last successful run per country; updates each country's state.json only on success."""
    logger.info("Starting TED ingestion | mode=incremental countries=%s storage_mode=%s", countries, storage_mode)

    for country in countries:
        iso3 = iso2_to_iso3(country)
        paths = country_paths(country)
        state = load_state(paths["state"], storage_mode)
        since_date = state.get("last_successful_run_date")

        if since_date:
            logger.info("Starting incremental load | country=%s from=%s", country, since_date)
        else:
            logger.warning(
                "No prior state found | country=%s — this will fetch everything matching "
                "the filters (first run)", country,
            )

        total_received = 0
        total_new = 0
        total_dup = 0
        batch_count = 0
        start_time = time.monotonic()

        try:
            query = build_query(iso3, since_date=since_date, sort=True)
            logger.debug("Incremental query | country=%s: %s", country, query)

            seen = load_existing_publication_numbers(paths["dataset"], storage_mode)
            logger.info("Existing publication-numbers on disk | country=%s count=%s", country, len(seen))

            for batch in paginate_iteration(query):
                batch_count += 1
                total_received += len(batch)
                new_count, dup_count = append_batch_jsonl(paths["dataset"], batch, seen, storage_mode)
                total_new += new_count
                total_dup += dup_count
                logger.info(
                    "Batch saved | country=%s batch=%s new=%s duplicates=%s",
                    country, batch_count, new_count, dup_count,
                )
        except Exception:
            logger.exception("Incremental ingestion failed | country=%s — state.json will NOT be updated", country)
            raise  # do not update state on failure

        elapsed = time.monotonic() - start_time

        logger.info("Incremental ingestion finished | country=%s", country)
        logger.info(
            "Incremental ingestion summary | country=%s requests=%s received=%s "
            "new_saved=%s duplicates=%s elapsed=%.1fs",
            country, batch_count, total_received, total_new, total_dup, elapsed,
        )

        # Only update state after full success
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        updated = update_state_if_newer(paths["state"], today, storage_mode)
        if updated:
            logger.info(
                "Incremental load completed successfully. State updated | "
                "country=%s last_successful_date=%s", country, today,
            )
        else:
            logger.info("Incremental load completed successfully. State left unchanged | country=%s", country)


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def run(mode: str, storage_mode: str = "local", countries: list[str] | None = None,
        from_date: str | None = None, to_date: str | None = None):
    """
    Called from root main.py:

        from ingestion.ted.notices import run as run_ted_ingestion
        run_ted_ingestion(mode="test")

    CLI argument parsing lives in main.py, not here.
    """
    countries = countries or DEFAULT_COUNTRIES
    if mode == "test":
        run_test(countries, storage_mode)
    elif mode == "historical":
        run_historical(countries, storage_mode, from_date=from_date, to_date=to_date)
    elif mode == "incremental":
        run_incremental(countries, storage_mode)
    else:
        raise ValueError(
            f"Unknown mode {mode!r} — expected 'test', 'historical', or 'incremental'"
        )


if __name__ == "__main__":
    run(
        mode="test",              # Run mode: test, historical, or incremental
        storage_mode="local",    # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
        countries=["DE"],        # e.g. ["DE", "PL"] — ISO2, one TED query per country
        from_date="2025-01-01",  # Start date for historical mode (YYYY-MM-DD)
        to_date="2025-01-31",    # End date for historical mode (YYYY-MM-DD)
    )
