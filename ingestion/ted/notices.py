"""
TED API v3 ingestion module — test / historical / incremental modes.

Not a standalone entry point — called from root main.py:

    from ingestion.ted.notices import run as run_ted_ingestion
    run_ted_ingestion(mode="test")
    run_ted_ingestion(mode="historical", from_date="2025-01-01", to_date="2025-01-31")
    run_ted_ingestion(mode="incremental")

Saves notices exactly as TED returns them for the requested FIELDS — no
field stripping, no language trimming. That JSON reshaping happens in
normalization.ted.notices. Country scope (buyer-country) is applied
server-side, inside the TED query string itself — TED's expert query
language processes it before any notices are returned, so this is not a
post-fetch filter.

The publication-number dedup here is a storage/idempotency concern, not a
data-cleaning one: it stops repeated incremental runs from re-appending
notices already on disk. It stays in ingestion for that reason — see the
project docs for more on this distinction.

Storage is local JSON/JSONL (data/raw/ted/), kept separate from the API/
pagination logic so it can later be swapped for a real DB without touching
the ingestion code.

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
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ted.europa.eu/v3/notices/search"

OUT_DIR = Path("data/raw/ted")
OUT_DIR.mkdir(parents=True, exist_ok=True)

TEST_PATH = OUT_DIR / "test_ingestion.json"
DATASET_PATH = OUT_DIR / "notices.jsonl"
STATE_PATH = OUT_DIR / "state.json"

ISO3 = "DEU"  # server-side filter — buyer-country is a documented TED query field

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

def build_query(from_date: str = None, to_date: str = None,
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
        f"buyer-country={ISO3}",
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

def load_existing_publication_numbers(path: Path) -> set:
    seen = set()
    if not path.exists():
        return seen
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                seen.add(json.loads(line).get("publication-number"))
            except json.JSONDecodeError:
                continue
    return seen


def append_batch_jsonl(path: Path, batch: list, seen: set) -> tuple:
    """Append new notices to JSONL, skipping existing publication-numbers."""
    new_count = 0
    dup_count = 0
    with open(path, "a", encoding="utf-8") as f:
        for notice in batch:
            pub_num = notice.get("publication-number")
            if pub_num in seen:
                dup_count += 1
                continue
            f.write(json.dumps(notice, ensure_ascii=False) + "\n")
            seen.add(pub_num)
            new_count += 1
    return new_count, dup_count


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def save_state(state: dict):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def update_state_if_newer(new_date: str) -> bool:
    """
    Set last_successful_run_date = new_date, unless state.json already has
    a date that's the same or later (never move the cursor backwards —
    e.g. a historical backfill for an old period shouldn't undo progress
    already made by incremental runs). Returns True if state was updated.
    """
    state = load_state()
    current = state.get("last_successful_run_date")
    if current and current >= new_date:
        logger.info(
            "State not updated — existing last_successful_run_date=%s is "
            "not earlier than %s", current, new_date,
        )
        return False
    state["last_successful_run_date"] = new_date
    save_state(state)
    return True


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------

def run_test():
    """One record, PAGE_NUMBER mode. Does not touch state.json or the dataset."""
    logger.info("Starting TED ingestion | mode=test")
    payload = {
        "query": build_query(sort=True),
        "fields": FIELDS,
        "page": 1,
        "limit": 1,
        "paginationMode": "PAGE_NUMBER",
    }
    data = call_api(payload)
    logger.info("API request completed | status=200")
    logger.debug("Response top-level keys: %s", list(data.keys()))

    notices = data.get("notices", data.get("results", []))
    if not notices:
        logger.warning("Test request returned 0 notices — check the query filters")
        return

    logger.info("Notices received | count=%s", len(notices))

    TEST_PATH.write_text(json.dumps(notices, ensure_ascii=False, indent=2))
    logger.info("Test ingestion saved | path=%s", TEST_PATH.resolve())

    # Summary at INFO; full record only at DEBUG (it's a sizeable blob)
    first = notices[0]
    logger.info(
        "Sample notice | publication_number=%s notice_type=%s publication_date=%s",
        first.get("publication-number"), first.get("notice-type"),
        first.get("publication-date"),
    )
    logger.debug("Full sample notice: %s", json.dumps(first, ensure_ascii=False))


def run_historical(from_date: str = None, to_date: str = None):
    """Full pagination via ITERATION mode, saved batch-by-batch as JSONL."""
    logger.info("Starting TED ingestion | mode=historical from_date=%s to_date=%s",
                from_date, to_date)
    start_time = time.monotonic()

    total_received = 0
    total_new = 0
    total_dup = 0
    batch_count = 0

    try:
        query = build_query(from_date=from_date, to_date=to_date, sort=True)
        logger.debug("Historical query: %s", query)

        seen = load_existing_publication_numbers(DATASET_PATH)
        logger.info("Existing publication-numbers on disk | count=%s", len(seen))

        for batch in paginate_iteration(query):
            batch_count += 1
            total_received += len(batch)
            new_count, dup_count = append_batch_jsonl(DATASET_PATH, batch, seen)
            total_new += new_count
            total_dup += dup_count
            logger.info(
                "Batch saved | batch=%s new=%s duplicates=%s running_total=%s",
                batch_count, new_count, dup_count, len(seen),
            )
    except Exception:
        logger.exception("Historical ingestion failed — state.json will NOT be updated")
        raise

    elapsed = time.monotonic() - start_time
    output_size = DATASET_PATH.stat().st_size if DATASET_PATH.exists() else 0

    logger.info("Historical ingestion finished")
    logger.info(
        "Historical ingestion summary | requests=%s received=%s "
        "unique_on_disk=%s duplicates=%s new_saved=%s elapsed=%.1fs "
        "output_size_kb=%.1f output_path=%s",
        batch_count, total_received, len(seen), total_dup, total_new,
        elapsed, output_size / 1024, DATASET_PATH,
    )

    # Only a bounded range (explicit to_date) represents a fully-covered
    # period we can safely record as "successfully loaded through". An
    # open-ended historical load (no to_date) has no such endpoint, so we
    # deliberately don't touch state.json in that case.
    if to_date:
        updated = update_state_if_newer(to_date)
        if updated:
            logger.info(
                "Historical load completed successfully. State updated: "
                "last_successful_date=%s", to_date,
            )
        else:
            logger.info(
                "Historical load completed successfully. State left "
                "unchanged (see reason above)."
            )
    else:
        logger.info(
            "Historical load completed successfully. No --to-date given — "
            "state.json left unchanged."
        )


def run_incremental():
    """Loads notices since last successful run; updates state.json only on success."""
    logger.info("Starting TED ingestion | mode=incremental")
    state = load_state()
    since_date = state.get("last_successful_run_date")

    if since_date:
        logger.info("Starting incremental load from: %s", since_date)
    else:
        logger.warning(
            "No prior state found — this will fetch everything matching "
            "the filters (first run)"
        )

    total_received = 0
    total_new = 0
    total_dup = 0
    batch_count = 0
    start_time = time.monotonic()

    try:
        query = build_query(since_date=since_date, sort=True)
        logger.debug("Incremental query: %s", query)

        seen = load_existing_publication_numbers(DATASET_PATH)
        logger.info("Existing publication-numbers on disk | count=%s", len(seen))

        for batch in paginate_iteration(query):
            batch_count += 1
            total_received += len(batch)
            new_count, dup_count = append_batch_jsonl(DATASET_PATH, batch, seen)
            total_new += new_count
            total_dup += dup_count
            logger.info(
                "Batch saved | batch=%s new=%s duplicates=%s",
                batch_count, new_count, dup_count,
            )
    except Exception:
        logger.exception("Incremental ingestion failed — state.json will NOT be updated")
        raise  # do not update state on failure

    elapsed = time.monotonic() - start_time

    logger.info("Incremental ingestion finished")
    logger.info(
        "Incremental ingestion summary | requests=%s received=%s "
        "new_saved=%s duplicates=%s elapsed=%.1fs",
        batch_count, total_received, total_new, total_dup, elapsed,
    )

    # Only update state after full success
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    updated = update_state_if_newer(today)
    if updated:
        logger.info(
            "Incremental load completed successfully. State updated: "
            "last_successful_date=%s", today,
        )
    else:
        logger.info("Incremental load completed successfully. State left unchanged.")


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def run(mode: str, from_date: str | None = None, to_date: str | None = None):
    """
    Called from root main.py:

        from ingestion.ted.notices import run as run_ted_ingestion
        run_ted_ingestion(mode="test")

    CLI argument parsing lives in main.py, not here.
    """
    if mode == "test":
        run_test()
    elif mode == "historical":
        run_historical(from_date=from_date, to_date=to_date)
    elif mode == "incremental":
        run_incremental()
    else:
        raise ValueError(
            f"Unknown mode {mode!r} — expected 'test', 'historical', or 'incremental'"
        )


if __name__ == "__main__":
    run(
        mode="test",              # Run mode: test, historical, or incremental
        from_date="2025-01-01",  # Start date for historical mode (YYYY-MM-DD)
        to_date="2025-01-31",    # End date for historical mode (YYYY-MM-DD)
    )
