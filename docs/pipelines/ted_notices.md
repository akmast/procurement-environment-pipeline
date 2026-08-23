# TED procurement notices

## What this pipeline gets

Contract award notices (`notice-type=can-standard`) from one or more EU
member states, whose CPV classification matches a fixed list of
environment-related codes (waste, sewage, water, cleaning services,
etc). One row per notice — buyer, winner, value, dates, place of
performance, CPV code, and a few other fields we asked for. Supports
multiple countries in one run (`countries=["DE", "PL"]`, ISO2) — see
`docs/pipelines/countries.md` for the mechanics shared across pipelines,
including the ISO2/ISO3 conversion this source needs internally.

## Source

TED (Tenders Electronic Daily) API v3. Official developer documentation:
https://docs.ted.europa.eu/api/latest/index.html (overview at
https://ted.europa.eu/en/ted-api/documentation, interactive Swagger at
https://api.ted.europa.eu/swagger).

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
republishes under a new number), so it needs a real historical/refresh
run to check, not just a small test call. `historical`/`refresh` log
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

For each requested country (ISO2, e.g. `"DE"`), converts it to the ISO3
code TED's query language expects (`buyer-country=DEU` — see
`docs/pipelines/countries.md` for why TED needs this conversion and the
other sources don't), fetches notices in batches via
`paginate_iteration()`, and appends each batch to
`data/raw/ted/<country>/notices.jsonl`, one JSON object per line,
**exactly as TED returns it** for the fields we asked for, with one
deliberate exception applied by `trim_heavy_fields()` before each
notice is written: the `links` block (PDF/XML/HTML URLs in all 24 EU
languages — always present in the response regardless of `FIELDS`,
TED doesn't support filtering it out server-side) is dropped, and
`notice-title` (also translated by TED into all 24 languages) is
trimmed to English plus the buyer country's own official language(s)
(`TED_LANGUAGE_BY_COUNTRY`). Both were large, never read by
normalization/transformation, and were bloating raw storage for no
benefit — this is the only place this project trims API response
content before it's even written to raw storage; every other field,
and every other language variant of `buyer-name`/`winner-name`/
`buyer-city` (which TED only ever populates in one language to begin
with), is kept as-is. No added columns — reshaping happens in
normalization.

Two mechanisms live in ingestion, not normalization, because they're
about *how raw data gets persisted*, not about *cleaning it* — both
scoped per country:

- **Publication-number dedup** — before appending, we check
  `publication-number` against everything already on disk for that
  country (`load_existing_publication_numbers`) so re-running
  `refresh` or overlapping `historical` ranges doesn't duplicate raw
  storage. This is storage idempotency, not a data-quality dedup pass.
- **`data/raw/ted/<country>/state.json`** —
  `{"last_successful_run_date": "..."}`, updated only after that
  country's batch finishes successfully, so `refresh` knows where to
  resume per country, and a failed run never silently loses progress.

Three modes, each looped over `countries`:

- **test** — one notice per country, `PAGE_NUMBER` pagination, doesn't
  touch `state.json` or `notices.jsonl`.
- **historical** — full `ITERATION` pagination over an explicit date
  range (or open-ended from a start date), per country.
- **refresh** — same, but each country's date range comes from its
  own `state.json["last_successful_run_date"]`. Named to match the
  same mode on `ingestion.eea.measurements` — see
  `docs/pipelines/countries.md` and `docs/storage_and_incremental.md`
  for what "refresh" means for each source (TED has no reporting-window
  mutable-years logic like EEA measurements does; here it's purely
  "since the last successful run").

## Normalization — `normalization/ted/notices.py`

Turns the raw JSONL into a compact analytical table — one row per
notice, typed columns, no nested structure — written to
`data/normalized/ted/<country>/notices.parquet`. This is a real reshape,
not a straight copy: the real API response (confirmed live, 2026-08-20)
turned out to have a few quirks that had to be handled explicitly rather
than assumed from the request `FIELDS` list:

- **Fields missing from a notice are omitted entirely**, never present
  as `null` — every field is read defensively.
- **Almost every "scalar" field is still wrapped in a single-element
  list** (e.g. `"buyer-country": ["DEU"]`, `"total-value-cur": ["EUR"]`)
  — unwrapped to a plain value (`unwrap_scalar()`).
- **Multilingual fields** (`buyer-name`, `winner-name`, `notice-title`,
  `buyer-city`) come back as `{lang: [value, ...]}`. Resolved to one
  string per notice via `resolve_language_field()`, preferring German →
  English → TED's language-neutral `"mul"` tag (seen used for
  `buyer-city`, e.g. `{"mul": ["Nürnberg"]}` — city names usually
  aren't translated) → whatever's left.
- **`place-of-performance` mixes NUTS codes and ISO3 country codes in
  one flat list**, e.g. `["DE236", "DEU"]` — not `"BT-5071-Lot"`, the
  business term actually requested, which never appears as its own key
  in real responses. Split apart by shape in
  `split_place_of_performance()`: a NUTS code is 2 letters + 1-3 digits,
  a bare ISO3 country code is 3 letters with no digit.
- **`classification-cpv` can repeat the same code** (one real notice
  listed each of 4 codes twice) — deduplicated, order preserved.

For each country (explicit, or every country found under
`data/raw/ted/`), every notice becomes one row with these columns:

```
country_code, publication_number, notice_type, notice_title,
publication_date, contract_conclusion_date,
buyer_name, buyer_country, buyer_city, buyer_post_code,
winner_name, winner_selection_status,
total_value, total_value_currency,
classification_cpv,               # list[str], deduplicated
non_award_justification,          # str
green_procurement_criteria,       # list[str], deduplicated (one entry per lot in the raw data)
nuts, nuts1, nuts2, nuts3,         # primary NUTS code + its levels by prefix
nuts_codes,                        # list[str] — every NUTS code found, not just the primary one
place_of_performance_country       # list[str] — the ISO3 entries split out of place-of-performance
```

`buyer_country` is `list[str]` — TED can list more than one buyer
country on a joint-procurement notice, confirmed live; kept as a list
even for the common single-value case (`unwrap_multi()`), never a bare
scalar, so the column has one stable type across every row (a raw list
in what pyarrow expected to be a plain string column previously crashed
`to_parquet()` with `ArrowTypeError: Expected bytes, got a 'list'
object`). Every other business field above that TED wraps in a
single-element list (`notice_type`, `buyer_post_code`,
`winner_selection_status`, `total_value_currency`,
`non_award_justification`, `publication_number`) is unwrapped to a
plain scalar via `unwrap_required_scalar()` — on the rare notice where
TED unexpectedly sends more than one value, they're joined into one
string and logged rather than silently keeping only the first.
`validate_column_types()` checks every column against this contract
right before `to_parquet()`, naming the specific column and unexpected
type if it's ever violated.

`nuts`/`nuts1`/`nuts2`/`nuts3` come from the **first** NUTS code found in
`place-of-performance` (nesting by prefix, same convention as
`transformation.eea.stations` — see `docs/pipelines/eea_stations.md`);
the full set is kept in `nuts_codes` so a multi-lot notice spanning
several regions doesn't lose that information. `country_code` is this
project's own ISO2 code (from the file's own directory) — a different
code space from `buyer_country` (TED's own ISO3 field(s)).

`links` (all `pdf`/`html`/`htmlDirect`/`xml` blocks — technical URLs,
not analytical data) and most of `notice-title`'s language variants
never even reach this stage — both are already trimmed at ingestion
time (`ingestion.ted.notices.trim_heavy_fields()`), not here.

Three fields needed a real populated sample to pin down (confirmed
2026-08-20):

- `total_value` — a bare float (e.g. `6054986.63`), unlike almost every
  other field, **not** list-wrapped.
- `non_award_justification` — a single-element list wrapping one
  controlled code (e.g. `["ins-fund"]`), same shape as
  `winner_selection_status`.
- `green_procurement_criteria` — a genuine **multi-element** list (e.g.
  `["other", "other"]`), one entry per lot the criterion applies to, so
  the same code can repeat. Deduplicated the same way as
  `classification_cpv` — this table is per-notice, not per-lot, so the
  set of distinct criteria is what matters at this grain.

## Transformation — `transformation/ted/notices.py`

1. **Dedup** by `publication_number` — a defensive safety net (ingestion
   already dedups on write; this is the same "cheap deterministic
   guarantee" reasoning as `transformation.eea.stations` re-deduping
   station codes).
2. **Codelist labeling** — joins in human-readable labels from the
   normalized TED codelists (see `docs/pipelines/ted_codelists.md`) for
   every coded field that's actually useful to interpret, without
   replacing the original code — both are kept:
   - `notice_type` → `notice_type_label`
   - `total_value_currency` → `total_value_currency_label`
   - `winner_selection_status` → `winner_selection_status_label`
   - `non_award_justification` → `non_award_justification_label`
   - `nuts`, `nuts1`, `nuts2`, `nuts3` → `nuts_label`, `nuts1_label`,
     `nuts2_label`, `nuts3_label` — all four resolved from the single
     `nuts` codelist, which lists every NUTS level in one table, so no
     separate codelist is needed per granularity.
   - `buyer_country` → `buyer_country_labels` — `buyer_country` is
     `list[str]` (normalization keeps it as a list since a
     joint-procurement notice can name more than one buyer country), so
     it's joined the same list-per-code way as the other two list
     columns below, against the `country` codelist — a plain scalar
     `.map()` (this project's old behavior, before `buyer_country`
     became list-valued) can't match a list against a scalar code key.
   - `classification_cpv` → `classification_cpv_labels` — one label per
     code, same order, `None` for any code missing from the CPV
     codelist.
   - `place_of_performance_country` → `place_of_performance_country_labels`
     — same list-join pattern, against the `country` codelist (this is
     the place-of-performance country, not `buyer_country` — a
     cross-border notice can have both, e.g. a German buyer with
     performance partly in Poland).

   Label preference per codelist row: `deu_label` → `eng_label` →
   `Name` → the code itself. A codelist that failed to load, or a code
   with no match, produces a `null` label rather than failing the run —
   labeling is enrichment, not a required field. Each codelist is
   loaded once per run and reused across every country (codelists are
   EU-wide reference data, not country-scoped).

   **Codelist coverage** — not every coded-looking column gets a join.
   `green_procurement_criteria` is a controlled-vocabulary-looking field
   (values observed so far: `"other"`) but no matching TED codelist is
   currently downloaded by `ingestion.ted.codelists` — it's kept as raw
   codes, unlabeled. The eForms SDK likely has one (business term BT-06,
   "Strategic Procurement" — a plausible codelist id is
   `strategic-procurement`, unconfirmed — see
   https://github.com/OP-TED/eForms-SDK/tree/main/codelists to find the
   real filename, the same way `nuts.gc` was confirmed). If this field
   turns out to matter for analysis, add it to `CODELISTS` in
   `ingestion/ted/codelists.py` and a join to `CODELIST_JOINS` here.

Writes `data/transformed/ted/<country>/notices.parquet`.

## Data flow

```
POST /notices/search (buyer-country=<ISO3> AND notice-type=... AND CPV AND date range)
        │  one query per country
        ▼
JSON batch of up to 250 notices (full TED shape) + iterationNextToken
        │
        ▼
trim_heavy_fields(): drop links, trim notice-title to eng + local language
        │
        ▼
append new (by publication-number) to data/raw/ted/<country>/notices.jsonl
        │
        ▼
normalization.ted.notices.run()
        │  unwrap list-wrapped scalars, resolve multilingual fields,
        │  split place-of-performance into NUTS + country, dedupe CPV,
        │  parse dates, stamp country_code
        ▼
data/normalized/ted/<country>/notices.parquet
        │
        ▼
transformation.ted.notices.run()
        │  dedup by publication_number
        │  + join code -> label from normalized TED codelists
        ▼
data/transformed/ted/<country>/notices.parquet
```

See `docs/pipelines/countries.md` for the `countries` parameter's shared
mechanics across all pipelines, and `docs/pipelines/ted_codelists.md`
for the codelists this transformation joins against.
