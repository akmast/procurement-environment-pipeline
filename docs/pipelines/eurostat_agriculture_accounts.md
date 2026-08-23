# Eurostat regional agricultural accounts

## What this pipeline gets

Annual **Regional Economic Accounts for Agriculture**, by NUTS2 region:
output value, intermediate consumption, gross/net value added, factor
income, operating surplus, subsidies, taxes, and related economic
indicators. Values are economic values in a monetary unit — this
pipeline requests `unit=MIO_EUR` (million euro) — **not physical
production volumes** (tonnes, hectares, etc.).

## Source

Eurostat Statistics API, JSON-stat 2.0 format, no authentication.

```
GET https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/aact_eaa01_r
```

**Dataset code:** `aact_eaa01_r` — "Economic accounts for agriculture by
NUTS 2 region - values at current prices". The older code
`agr_r_accts` used by an earlier draft of this ingestion is retired;
`aact_eaa01_r` is Eurostat's current dataset for this data.

**Real example request** (`ingestion.eurostat.agriculture_accounts.fetch_country_year`):
NUTS2 region codes are discovered first (`geo` and `geoLevel` can't be
combined in one request), then the actual fact request repeats `geo=`
once per region:

```
GET .../data/aact_eaa01_r?lang=EN&time=2021&unit=MIO_EUR&geo=DE11&geo=DE12&geo=DE13&...
```

**Reference links:**
- [Dataset in the Eurostat Data Browser](https://ec.europa.eu/eurostat/databrowser/view/AACT_EAA01_R/default/table?lang=en)
- [Statistics API — getting started](https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-getting-started/api)
- [Statistics API — detailed guidelines](https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-detailed-guidelines/api-statistics)
- [API data access overview](https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access)
- [JSON-stat 2.0 format specification](https://json-stat.org/format/)

## Raw response format: JSON-stat 2.0

A JSON-stat 2.0 dataset represents a multidimensional statistical cube
in compressed form — it is reconstructed from four parts:

```
id + size + dimension + value
```

- **`id`** — the dimension order, e.g. for this dataset:
  `["freq", "am_item", "indic_agr", "unit", "geo", "time"]`
  (frequency, agricultural/economic item, indicator type, unit, NUTS2
  region, year).
- **`size`** — the number of categories in each dimension, same order
  as `id`. A real DE/2021 response: `[1, 90, 4, 1, 38, 1]` — 1 freq ×
  90 am_item × 4 indic_agr × 1 unit × 38 geo × 1 time = 13680 possible
  cells.
- **`dimension`** — per-dimension category codes, their numeric
  position, and human-readable labels:
  ```json
  "geo": {
    "category": {
      "index": {"DE11": 0, "DE12": 1},
      "label": {"DE11": "Stuttgart", "DE12": "Karlsruhe"}
    }
  }
  ```
  `category.index` can be a plain array (position = array index) or,
  as in every dimension of this dataset's real responses, an object
  mapping `{code: position}`. The object's own key order is **not**
  guaranteed by the JSON-stat spec to match position order, so codes
  must always be resolved by sorting on the position *value* — never
  by assuming iteration order (see `category_codes()` in both
  `ingestion` and `normalization`).
- **`value`** — the actual observations, keyed by **flat index**, not
  by dimension code:
  ```json
  "value": {"0": 273.94, "1": 124.54, "38": 273.94, "152": 155.41}
  ```
  A flat index is decoded back into one position per dimension using
  `id`'s order and `size`: dimension `i`'s stride is the product of
  every size listed after it (`stride[i] = product(size[i+1:])`), and
  its position at a given flat index is `(flat_index // stride[i]) %
  size[i]`. For `size = [1, 90, 4, 1, 38, 1]`, two decoded examples:
  ```
  index 0   -> Cereals (including seeds), Production value at basic price,
               Stuttgart (DE11), 2021 -> 273.94 million euro
  index 152 -> Wheat and spelt, Production value at basic price,
               Stuttgart (DE11), 2021 -> 155.41 million euro
  ```
- **`status`** *(optional)* — flags *why* a cell has no value, keyed by
  the same flat-index space as `value`, but its keys never overlap
  with `value`'s. Real responses use `"m"` (per SDMX's `CL_OBS_STATUS`:
  "data cannot exist for this combination"). A `status` entry is not a
  numeric observation — it doesn't become a row in the normalized
  table, and a missing value is never replaced with `0`.
- **`extension`** *(optional)* — dataset-level metadata (DOI, source
  annotations); not used by this pipeline.

## Filled vs. empty responses

A structurally valid JSON-stat response can legitimately carry **zero
observations** — this is Eurostat's own "not published yet" result,
not an error. Example, Poland/2025 (a year not yet published):

```json
{
  "value": {},
  "id": ["freq", "am_item", "indic_agr", "unit", "geo", "time"],
  "size": [1, 90, 4, 1, 17, 0],
  "dimension": {
    "time": { "category": { "index": {}, "label": {} } }
  }
}
```

The structure, dimension categories, and regions are all present —
only `time`'s own `size` is `0` (so the cube's total size is `0` too)
and `value` is empty. `normalization.eurostat.agriculture_accounts`
treats this as `NO_DATA`: the raw file is recorded as unchanged
(`StageResult.record_unchanged`), no parquet is written, and the stage
does not fail — see that module's `is_valid_empty_result()`.

Other things to keep in mind about `value`:
- `0.0` is a real observation, not a missing one — never dropped via a
  truthiness check.
- Negative values are valid and preserved as-is.
- A missing observation (`null`, or simply absent from `value`) is
  never replaced with `0`.
- A `status`-flagged cell is not a numeric observation, even though it
  shares `value`'s flat-index space.

## From raw JSON-stat to the normalized table

Each real observation in `value` becomes one row of a flat,
long-format table — one row per **actual** numeric observation,
including zero and negative values. Every dimension is kept as both
its raw code and Eurostat's own label:

| Column | Source | dtype |
|---|---|---|
| `country_code` | ISO2, from the raw file's own directory | string |
| `freq` / `freq_label` | `freq` category code / label | string |
| `am_item` / `am_item_label` | `am_item` category code / label | string |
| `indic_agr` / `indic_agr_label` | `indic_agr` category code / label | string |
| `unit` / `unit_label` | `unit` category code / label | string |
| `geo` / `geo_label` | `geo` category code (NUTS2, e.g. `"DE11"`) / label | string |
| `time` / `time_label` | `time` category code / label | `Int64` / string |
| `value` | the observation itself | `float64` |

Example row (DE/2021, flat index 0 above):

| country_code | freq | am_item | indic_agr | unit | geo | geo_label | time | value |
|---|---|---|---|---|---|---|---:|---:|
| DE | A | AM010000 | PRD_BP | MIO_EUR | DE11 | Stuttgart | 2021 | 273.94 |

This is a mechanical reshape, not a business transformation: dimension
codes are kept exactly as Eurostat returns them — nothing is renamed
into domain concepts, no NUTS-level splitting, no `am_item`/`indic_agr`
filtering, no joins. That's left for a later, more opinionated
transformation step.

## Country and NUTS2 region selection

`geo=DE` alone selects Germany's *national* total, not its NUTS2
regions, and the API rejects a request that sets both `geo` and
`geoLevel`. So region codes are discovered first via a small
`geoLevel=nuts2` query, then the real fact request sends those exact
codes as repeated `geo=` parameters — country filtering happens
server-side, not by discarding downloaded rows. Germany has 38 NUTS2
regions and Poland 17, both well under the API's 50-values-per-filter
limit.

### Latest available year

Different countries publish a given year at different times, so "the
latest year" is discovered per country rather than hardcoded — and by
checking actual value presence for that country's own region codes,
not just whether a year appears in the `time` dimension's category
list (Eurostat can list a year as a category before every country has
actually submitted data for it).

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
  snapshots and can revise already-published values, so there's no
  append-only record stream or `created_at` cursor to follow — this
  can't work like TED's "since last successful date" refresh
  (`docs/pipelines/ted_notices.md`). Re-checking every tracked year
  catches both new periods and silent revisions to old ones; DE/PL's
  annual subsets are small enough that this is cheap. A first refresh
  with no prior state raises unless `from_year` is passed explicitly —
  there's no safe default starting point to guess.

## Normalization — `normalization/eurostat/agriculture_accounts.py`

Melts each raw JSON-stat cube into the flat table described above,
mirroring the same `<country>/<year>/...` layout under
`data/normalized/eurostat/regional_agricultural_accounts/`. A
structurally valid response with no observations (see "Filled vs.
empty responses" above) writes nothing and is recorded as unchanged,
not failed. A structurally inconsistent file (missing required fields,
an out-of-range category position or flat index, an observation value
of an unexpected type) fails loudly and is recorded in the stage's
`failed_paths` — see `validate_json_stat_structure()`.

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
              │  validate structure, decode JSON-stat cube
              │
         ┌────┴────┐
     NO_DATA      has observations
    (value empty,      │
   any size==0, or      ▼
  time index empty)   one row per observation, code + label per
         │             dimension, typed time/value
         ▼                  │
   record unchanged,        ▼
    no file written    data/normalized/eurostat/regional_agricultural_accounts/<country>/<year>/aact_eaa01_r.parquet
```
