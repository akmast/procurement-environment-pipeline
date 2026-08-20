# Eurostat regional agricultural accounts

## What this pipeline gets

Annual regional economic accounts for agriculture, by NUTS2 region:
output components, intermediate consumption, gross/net value added,
factor income, operating surplus, subsidies/taxes, capital formation.
Values are economic values in **million euro** — not physical production
volumes.

**Verification status:** outbound access to `ec.europa.eu` is blocked in
the development sandbox (same restriction as EEA/GISCO earlier in this
project — see `docs/pipelines/eea_nuts_boundaries.md`), so the API was
never called directly during development. The user ran `ingestion` and
`normalization` for real against the live API outside the sandbox
(2026-08-20, DE + PL, 2021-2023) — both worked end to end, no errors —
and shared one real raw response (`DE/2021`) back, which confirmed most
of the assumptions below and surfaced one previously-unseen field
(`status`, see below). Anything still marked unconfirmed genuinely
hasn't been observed yet, not just "not tested by me."

## Source

Eurostat Statistics API, JSON-stat 2.0 format, no authentication.

```
Method: GET
URL:    https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/aact_eaa01_r
```

**Dataset code:** `aact_eaa01_r` — "Economic accounts for agriculture by
NUTS 2 region - values at current prices" — confirmed as a real, current
dataset both via web search and by successfully downloading it live.
Its own metadata (`extension.annotation`, type `DISSEMINATION_DOI_XML`)
records a DOI issued `2025-11-28`, matching web search results describing
Eurostat publishing regional EAA "since 28 November 2025" — consistent
with `agr_r_accts` (the code this project's ingestion started from,
based on an older draft) being the retired predecessor. Whether
`agr_r_accts` itself actually 404s was never directly tested (nothing
in this project calls it) — irrelevant in practice now that
`aact_eaa01_r` is confirmed working.

**Response format (JSON-stat 2.0):** confirmed both via Eurostat's API
documentation and a real response. Key structure (real field set is
slightly larger than initially documented — see below):

```
{
  "version": "2.0", "class": "dataset",
  "label": "Economic accounts for agriculture by NUTS 2 region - values at current prices",
  "source": "ESTAT",
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
  "value": {"<flat_index>": <number>, ...},    # sparse form; confirmed live for aact_eaa01_r
  "status": {"<flat_index>": "<flag code>", ...},  # confirmed live — see "status flags" below
  "updated": "<ISO timestamp>",
  "extension": {...}   # dataset-level metadata (DOI, description, source annotations) — not used
}
```

## A bug found (and fixed) while reviewing the draft

`category.index` can be either an array (position = array index) **or**
an object mapping `{code: position}` — confirmed via web search against
the JSON-stat 2.0 spec example: `{"M": 0, "F": 1, "T": 2}`, and now also
confirmed live: `aact_eaa01_r`'s real response uses the object form for
every dimension (e.g. `"am_item": {"AM010000": 0, "AM011000": 1, ...}`),
not the array form. The draft implementation handled the object form as
`list(index)` — i.e. it took the dict's own key/insertion order and
assumed that matched position order. **The spec does not guarantee
that**, and this project can't assume Eurostat's real key order always
happens to match (the live sample's keys happened to already be in
position order, so this specific bug wouldn't have been caught just by
running against real data without a targeted test). If Eurostat's JSON
ever lists codes in an order that differs from their `position` values,
that would silently misassign every code in that dimension to the wrong
observations — a correctness bug, not a crash.

Fixed by always resolving codes from an object-form index via
`sorted(index.items(), key=lambda item: item[1])` — sorting on the
position *value*, never relying on iteration order. Verified with a unit
test using a deliberately shuffled key order (`{"DE13": 2, "DE11": 0,
"DE12": 1}`) and confirmed the correct codes end up matched to the
correct observations. See `ingestion/eurostat/agriculture_accounts.py`'s
`category_codes()`.

## `status` flags — a real field the initial design missed

Confirmed live: the response has an optional top-level `status` field,
`{flat_index: flag_code}` — same flat-index space as `value`, but its
keys **never overlap** with `value`'s keys. A cell either has a number
or a status flag explaining its absence, never both. For DE/2021
(90 am_item × 4 indic_agr × 38 geo × 1 time = 13680 possible cells):
4976 have a value, 4636 are flagged (all `"m"` in this sample — per
SDMX's CL_OBS_STATUS, "m" means the data point cannot exist for that
combination, not "missing/unknown"), and the remaining 4068 are simply
absent with no explanation at all.

Because the key spaces are disjoint, `status` can't be attached as a
column on the melted observations table — there's no row for it to sit
on. `normalization.eurostat.agriculture_accounts.summarize_status_flags()`
counts and logs it per file (`Cube cell accounting | ... with_status_flag=...`)
instead of silently dropping it. Whether it's worth carrying forward
(e.g. as a separate small table of "known-absent, and why" cells) is a
transformation-stage decision, not made here.

**Not yet needed as a separate downloaded reference:** the flag
vocabulary (SDMX's `CL_OBS_STATUS`) is a small, standard, well-known
code set — not a per-dataset Eurostat reference table like the `geo`/
`am_item` dimensions, which are already fully self-describing via their
own embedded `label` maps. If more flag codes than `"m"` show up in
practice and their meaning needs confirming beyond what's already
documented here, that would be the moment to look at pulling in
SDMX's/Eurostat's own flag legend — not needed for the current
normalization scope.

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

## Confirmed live (2026-08-20, real ingestion + normalization run)

- `aact_eaa01_r` is a real, working dataset; `id`/`size`/`dimension`/
  `value` all match what ingestion and normalization expect.
- `category.index` is object-form (`{code: position}`) for every
  dimension in this dataset, not array-form.
- `value` is the sparse dict form.
- Germany has 38 NUTS2 regions, Poland 17 — both far under the 50-value
  filter limit.
- `am_item` has 90 codes and `indic_agr` has 4 in the real cube (only 83
  and 2 of those respectively actually appear with a value for DE/2021 —
  sparse data, not every combination is populated).
- `ingestion.eurostat.agriculture_accounts.run()` and
  `normalization.eurostat.agriculture_accounts.run()` both completed
  without errors against real DE/PL data, 2021-2023.

## Still open

- The exact 50-values-per-filter and cell-count limits (found via web
  search for the API in general — 500k cells triggers async processing,
  5M is a hard error — not confirmed specific to this dataset, though
  DE/PL's real request sizes are nowhere near either threshold).
- Whether NUTS2 boundaries used elsewhere in this project
  (`docs/pipelines/eea_nuts_boundaries.md`, GISCO-sourced) use the same
  NUTS version/codes as Eurostat's own `geo` dimension here — not
  checked; matters once a transformation step tries to join the two.
- Full meaning of `status` flag codes beyond `"m"` (see above) if other
  codes turn up in practice.
