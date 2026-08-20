# TED procurement notices

## What this pipeline gets

Contract award notices (`notice-type=can-standard`) from Germany, whose
CPV classification matches a fixed list of environment-related codes
(waste, sewage, water, cleaning services, etc). One row per notice —
buyer, winner, value, dates, place of performance, CPV code, and a few
other fields we asked for.

## Source

TED (Tenders Electronic Daily) API v3.

```
Method: POST
URL:    https://api.ted.europa.eu/v3/notices/search

JSON body (paginated form):
{
    "query": "buyer-country=DEU AND notice-type=can-standard AND
               (classification-cpv=90000000 OR classification-cpv=71313000 OR ...)
               AND publication-date>=20250101 AND publication-date<=20250131
               SORT BY publication-date DESC",
    "fields": ["publication-number", "notice-title", "buyer-name", ...],
    "limit": 250,
    "scope": "ALL",
    "paginationMode": "ITERATION",
    "onlyLatestVersions": true,
    "iterationNextToken": "<from previous response, omitted on first call>"
}

Expected response:
JSON with a "notices" array, "totalNoticeCount", "iterationNextToken"
(present while more pages remain), and "timedOut" — confirmed via a live
3-notice test call on 2026-08-19.
```

`buyer-country`, `notice-type`, `classification-cpv` and `publication-date`
are documented TED expert-query search fields — the filtering happens
entirely on TED's side before anything is returned to us. This is not a
post-fetch filter.

**Confirmed (2026-08-19 live test):** the response keys above, and that
`SORT BY` works together with `paginationMode=ITERATION` (a valid
`iterationNextToken` came back). `timedOut: true` means TED's backend gave
up before finishing the search for that batch — `paginate_iteration()`
logs a warning when this happens, since the batch may be incomplete.

**Still open:** whether `publication-number` is a safe, collision-free
dedup key — this depends on data volume (e.g. whether a corrigendum
republishes under a new number), so it needs a real historical/incremental
run to check, not just a small test call. `historical`/`incremental` log
duplicate counts so this is checkable once run.

### Pagination — ITERATION mode

```
Request 1 (no token)  → up to 250 notices + iterationNextToken
Request 2 (token #1)  → up to 250 notices + iterationNextToken
Request 3 (token #2)  → up to 250 notices, no token
                              │
                              ▼
                    stop — pagination finished
```

We also stop early if `totalNoticeCount` is 0, or if a batch comes back
empty (defensive — TED has been observed to keep returning a token past
the end of results).

## Ingestion — `ingestion/ted/notices.py`

Reads/writes go through `common.storage` (`storage_mode="local"`/`"cloud"`
— see `docs/storage_and_incremental.md`). No content hashing here,
unlike the other three pipelines: notices are an append-only stream
already deduplicated per-record by `publication-number`, not a
redownloadable snapshot file — there's nothing to hash.

Fetches notices in batches via `paginate_iteration()` and appends each
batch to `data/raw/ted/notices.jsonl`, one JSON object per line, **exactly
as TED returns it** for the fields we asked for — all ~23 language
variants of `buyer-name`/`notice-title`, the `links` block, everything.
No field stripping, no language trimming — that happens in normalization.

Two mechanisms live in ingestion, not normalization, because they're
about *how raw data gets persisted*, not about *cleaning it*:

- **Publication-number dedup** — before appending, we check
  `publication-number` against everything already on disk
  (`load_existing_publication_numbers`) so re-running `incremental` or
  overlapping `historical` ranges doesn't duplicate raw storage. This is
  storage idempotency, not a data-quality dedup pass.
- **`state.json`** — `{"last_successful_run_date": "..."}`, updated only
  after a batch finishes successfully, so `incremental` knows where to
  resume and a failed run never silently loses progress.

Three modes:

- **test** — one notice, `PAGE_NUMBER` pagination, doesn't touch
  `state.json` or `notices.jsonl`.
- **historical** — full `ITERATION` pagination over an explicit date
  range (or open-ended from a start date).
- **incremental** — same, but the date range comes from
  `state.json["last_successful_run_date"]`.

## Normalization — `normalization/ted/notices.py`

Reads `data/raw/ted/notices.jsonl` line by line and, per notice:

1. `strip_unwanted()` — drops the `links` block and any field with
   "email" in its name.
2. `trim_languages()` — `buyer-name`/`notice-title` come back with ~23
   language keys; keeps only `deu` + `eng`.

Writes the result to `data/normalized/ted/notices.jsonl`, one JSON object
per line — no filtering by country here, ingestion already scoped the
query to Germany server-side.

## Data flow

```
POST /notices/search (buyer-country=DEU AND notice-type=... AND CPV AND date range)
        │
        ▼
JSON batch of up to 250 notices (full TED shape) + iterationNextToken
        │
        ▼
append new (by publication-number) to data/raw/ted/notices.jsonl
        │
        ▼
normalization: strip links/email, trim languages
        │
        ▼
data/normalized/ted/notices.jsonl
```
