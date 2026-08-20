# Eurostat regional agricultural accounts

## What this pipeline gets

Annual regional economic accounts for agriculture, by NUTS2 region:
output components, intermediate consumption, gross/net value added,
factor income, operating surplus, subsidies/taxes, capital formation.
Values are economic values in **million euro** — not physical production
volumes.

**Not independently verified in this sandbox:** outbound access to
`ec.europa.eu` is blocked here (same restriction as EEA/GISCO earlier in
this project — see `docs/pipelines/eea_nuts_boundaries.md`), so the
Statistics API was never actually called during development. Everything
below is based on (a) Eurostat's own published documentation and dataset
catalogue, cross-checked via web search where possible, and (b) careful
review — not blind reuse — of a draft implementation. Confirm on the
first real run, the same way `nuts.gc`'s filename was flagged earlier in
this project.

## Source

Eurostat Statistics API, JSON-stat 2.0 format, no authentication.

```
Method: GET
URL:    https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/aact_eaa01_r
```

**Dataset code:** the former online code `agr_r_accts` returns HTTP 404
on the current API (per the draft this project started from). Eurostat's
own product catalogue and databrowser confirm `aact_eaa01_r` —
"Economic accounts for agriculture by NUTS 2 region" — as a real,
current dataset, and web search results describe Eurostat publishing
regional EAA "since 28 November 2025," consistent with `agr_r_accts`
being an older/retired identifier. The exact 404-on-`agr_r_accts` claim
itself was not independently re-verified (would require calling the
blocked domain) — treat it as the starting hypothesis, not a confirmed
fact, until checked on a real run.

**Response format (JSON-stat 2.0):** confirmed via Eurostat's own API
documentation (found via web search — `ec.europa.eu/eurostat/web/...`
guides were not directly fetchable, but their content was described in
search results). Key structure:

```
{
  "class": "dataset",
  "id": ["freq", "am_item", "indic_agr", "unit", "geo", "time"],
  "size": [1, N_items, N_indic, 1, N_regions, N_years],
  "dimension": {
    "<dim>": {
      "category": {
        "index": {...} or [...],   # code -> position, or codes in position order
        "label": {"<code>": "<human-readable name>", ...}
      }
    }, ...
  },
  "value": {"<flat_index>": <number>, ...},   # sparse form; can also be a dense array
  "updated": "<ISO timestamp>"
}
```

## A bug found (and fixed) while reviewing the draft

`category.index` can be either an array (position = array index) **or**
an object mapping `{code: position}` — confirmed via web search against
the JSON-stat 2.0 spec example: `{"M": 0, "F": 1, "T": 2}`. The draft
implementation handled the object form as `list(index)` — i.e. it took
the dict's own key/insertion order and assumed that matched position
order. **The spec does not guarantee that.** If Eurostat's JSON ever
lists dimension codes in an order that differs from their `position`
values (alphabetical vs. a curated display order, for example), that
would silently misassign every code in that dimension to the wrong
observations — a correctness bug, not a crash, so it would go unnoticed
without a byte-for-byte check.

Fixed by always resolving codes from an object-form index via
`sorted(index.items(), key=lambda item: item[1])` — sorting on the
position *value*, never relying on iteration order. Verified with a unit
test using a deliberately shuffled key order (`{"DE13": 2, "DE11": 0,
"DE12": 1}`) and confirmed the correct codes end up matched to the
correct observations. See `ingestion/eurostat/agriculture_accounts.py`'s
`category_codes()`.

## Country and NUTS2 region selection

`geo=DE` selects Germany's *national* total, not its NUTS2 regions —
and, per Eurostat's own API guidelines (confirmed via web search), `geo`
and `geoLevel` are mutually exclusive: a request cannot set both. So:

1. A small discovery request (`geoLevel=nuts2`, one narrow output series,
   one year) returns every NUTS2 code that exists for that year; region
   codes belonging to the requested country are picked out by prefix
   (`code.startswith("DE")`).
2. The real fact request sends those exact codes as repeated `geo=`
   parameters — country filtering happens server-side, not by discarding
   downloaded rows.

The API accepts at most 50 values for one dimension filter (per the
draft's claim, not independently re-verified) — Germany and Poland's
NUTS2 counts both fit comfortably within that.

### Latest available year

Different countries publish a given year at different times — the draft
observed DE/PL still lacking 2024 while other countries already had it,
so "the latest year" is discovered per country rather than hardcoded.
This uses value presence (`codes_with_observations()`), not just whether
a year appears in the `time` dimension's category list — Eurostat's
`time` dimension can list a year as a category even before every country
has actually submitted data for it.

## Storage layout

```
data/raw/eurostat/regional_agricultural_accounts/
├── DE/
│   ├── 2022/aact_eaa01_r.json
│   ├── 2023/aact_eaa01_r.json
│   └── state.json
└── PL/
    ├── 2022/aact_eaa01_r.json
    ├── 2023/aact_eaa01_r.json
    └── state.json
```

One file per country/year, mirroring the same `<country>/<year>/...`
convention as EEA measurements (`docs/pipelines/eea_measurements.md`).
Reads/writes go through `common.storage`, so `storage_mode="local"`/
`"cloud"` run identically — see `docs/storage_and_incremental.md`.

## Ingestion — `ingestion/eurostat/agriculture_accounts.py`

Every response is staged, validated as a structurally valid JSON-stat
2.0 dataset (`common.validation.is_valid_json_stat` — has `class:
"dataset"` plus `id`/`size`/`dimension`/`value`), and only then
hash-compared against `state.json` before being promoted to final
storage — same staging/validation/hashing flow as every other source
(`docs/storage_and_incremental.md`).

Three modes, matching this project's unified mode naming
(`docs/pipelines/countries.md`):

- **test** — one narrow output series, latest available year, per
  country. Doesn't touch real storage or state.
- **historical** — downloads an explicit `from_year..to_year` range per
  country. `run(mode="historical", countries=["DE", "PL"], from_year=2021, to_year=2023)`.
- **refresh** — re-requests *every year already tracked* for the
  country, plus any newly available year. Eurostat publishes annual
  snapshots and can revise already-published values — there's no
  append-only record stream or `created_at` cursor to follow, so this
  can't work like TED's "since last successful date" refresh
  (`docs/pipelines/ted_notices.md`). Re-checking every tracked year
  catches both new periods and silent revisions to old ones; DE/PL's
  annual subsets are small enough that this is cheap. A first refresh
  with no prior state raises unless `from_year` is passed explicitly —
  there's no safe default starting point to guess.

`historical`/`refresh` both return `{country: {"files", "written",
"written_paths"}}` — `written_paths` is exactly which files changed,
meant to be passed straight into normalization (see
`docs/pipelines/countries.md`'s "only the files that actually changed"
pattern, already used by EEA measurements).

## Normalization — `normalization/eurostat/agriculture_accounts.py`

Melts each raw JSON-stat cube into a flat, long-format table — one row
per non-null observation — mirroring the same `<country>/<year>/...`
layout under `data/normalized/eurostat/regional_agricultural_accounts/`.
This is a mechanical reshape, not a business transformation: every
dimension keeps its own code *and* Eurostat's own human-readable label
as separate columns, nothing is renamed into domain concepts, no
NUTS-level splitting, no filtering to specific `am_item`/`indic_agr`
codes, no joins, no aggregation. That's deliberately left for a later
transformation step, once the real columns have been reviewed.

Output columns: `country_code` (this project's own ISO2, from the file's
directory — a different, more directly usable code space than `geo`),
`freq`/`freq_label`, `am_item`/`am_item_label`, `indic_agr`/
`indic_agr_label`, `unit`/`unit_label`, `geo`/`geo_label` (NUTS2 code,
e.g. `"DE11"`), `time` (cast to `Int64`) /`time_label`, `value` (cast to
`float`).

`countries` must be passed explicitly, same "explicit partitions only"
convention as every other normalization module in this project — see
`docs/pipelines/countries.md`.

## Data flow

```
GET .../data/aact_eaa01_r?geoLevel=nuts2&time=<year>&...   (region discovery)
        │
        ▼
NUTS2 codes for the requested country
        │
        ▼
GET .../data/aact_eaa01_r?geo=<code1>&geo=<code2>&...&time=<year>   (fact request)
        │
        ▼
write to staging, read back, validate as JSON-stat 2.0
        │
   ┌────┴────┐
invalid     valid
   │           │
   ▼           ▼
 skip    hash bytes, compare to state.json
              │
         ┌────┴────┐
       same      differ/new
         │           │
         ▼           ▼
       skip     data/raw/eurostat/regional_agricultural_accounts/<country>/<year>/aact_eaa01_r.json
              │
              ▼
        normalization.eurostat.agriculture_accounts.run()
              │  decode JSON-stat cube -> one row per observation,
              │  code + label per dimension, typed time/value
              ▼
        data/normalized/eurostat/regional_agricultural_accounts/<country>/<year>/aact_eaa01_r.parquet
```

## Open questions / things to confirm on a real run

- Whether `agr_r_accts` genuinely 404s and `aact_eaa01_r` is the correct
  replacement (strong circumstantial support from web search, not a
  direct API call).
- Whether the API's actual `category.index` responses for this dataset
  use the array form, the object form, or both depending on dimension —
  the decoding handles either, but which one Eurostat actually sends was
  never observed directly.
- The exact 50-values-per-filter and cell-count limits (found via web
  search for the API in general — 500k cells triggers async processing,
  5M is a hard error — not confirmed specific to this dataset).
- Whether NUTS2 boundaries used elsewhere in this project
  (`docs/pipelines/eea_nuts_boundaries.md`, GISCO-sourced) use the same
  NUTS version/codes as Eurostat's own `geo` dimension here — not
  checked; matters once a transformation step tries to join the two.
