# Multi-country support

Shared mechanics used across pipelines — described once here instead of
repeated in every pipeline doc. Each pipeline doc links back to this page
for the details.

## The `countries` parameter

Every `run(...)` across ingestion/normalization/transformation takes
`countries: list[str] | None = None`, using ISO2 codes (`"DE"`, `"PL"`,
...) — the same code space as the storage directory names below.

- **ingestion** — `countries` defaults to the pipeline's own
  `DEFAULT_COUNTRIES` (currently `["DE", "PL"]`) if not passed.
- **normalization/transformation** — `countries` is **required**; there
  is no default. Passing nothing raises a `ValueError` rather than
  silently scanning and processing every country on disk — see
  "Explicit partitions only" below for why, and how to opt into
  "everything" deliberately when that's actually what you want.

```python
run(mode="stations", countries=["DE", "PL"])          # ingestion.eea.stations
run(mode="historical", countries=["DE", "PL"],         # ingestion.eea.measurements
    from_year=2023, to_year=2025)
run(mode="historical", countries=["DE", "PL"],         # ingestion.ted.notices
    from_date="2025-01-01", to_date="2025-01-31")
run(countries=["DE", "PL"])                             # any normalization/transformation module
```

## Explicit partitions only — no implicit whole-directory processing

Normalization and transformation `run()` functions never default to
"scan the base directory and process everything found there" — that
used to be the behavior when `countries` was omitted (auto-discovery),
but it meant every run reprocessed the entire dataset regardless of how
much of it was actually new. `countries` (or `codelist_ids` for TED
codelists, which has no country concept) is now **required**, and the
caller decides its scope explicitly, two ways:

- **A subset** — one or more country codes (`["DE"]`), or for EEA
  measurements, a finer partition prefix at any granularity under its
  `<country>/<year>/<pollutant>/` layout (`["DE/2025"]`,
  `["DE/2025/PM10"]`), or an exact file path
  (`["data/raw/eea/measurements/DE/2025/PM10/some_file.parquet"]`).
  `common.storage.resolve_paths()` is what expands a mix of these into
  the flat file list actually processed — a prefix is listed, an exact
  path (one that already ends in the expected suffix) is used as-is.
- **Everything currently on disk, deliberately** — each module still
  exports its old auto-discovery logic as a plain function
  (`discover_countries(storage_mode)`, or `discover_codelist_ids` for
  codelists), so "process everything" is one explicit call away rather
  than a hidden default:

  ```python
  from normalization.eea.stations import run, discover_countries
  run(countries=discover_countries("local"))
  ```

### Refresh workflows: only the files that actually changed

This matters most for EEA measurements, where ingestion already only
(re)writes a file when its content-hash actually differs (see
`docs/storage_and_incremental.md`) — reprocessing the whole country on
every normalization/transformation run would throw that away and redo
work on files nothing changed in. `ingestion.eea.measurements.run()` in
`historical`/`refresh` mode returns exactly which files it wrote, so
that list can be handed straight to normalization/transformation:

```python
from ingestion.eea.measurements import run as ingest
from normalization.eea.measurements import run as normalize
from transformation.eea.measurements import run as transform

written = ingest(mode="refresh")
# {"DE": {"files": 40, "written": 3, "written_paths": [...]}, "PL": {...}}

new_files = [path for country in written.values() for path in country["written_paths"]]
if new_files:
    normalize(countries=new_files)
    transform(countries=new_files)   # normalized paths mirror the raw layout, same relative paths work
```

`written_paths` is empty for a country/run where nothing actually
changed — in that case there's nothing to normalize/transform, and the
caller should skip the call rather than pass an empty list (which
`run()` treats the same as "not provided" and rejects).

EEA stations and TED notices don't need this: ingestion writes exactly
one file per country each run (the whole station list, or the whole
notices append), so `countries=["DE"]` already is "just the data for
this country" — there's no finer-grained "only the new part" below the
country level for those two sources.

## Server-side filtering, one request per country

Where the source API supports filtering by country, that filter is
always used directly in the request — never applied after downloading:

| Source | Server-side filter |
|---|---|
| EEA stations (ArcGIS) | `where=CountryCode='<country>'` |
| EEA measurements | `countries: ["<country>"]` in the POST body |
| TED notices | `buyer-country=<ISO3>` in the query string |

Each pipeline loops over `countries` and makes **one request per
country** (for EEA measurements, one request per country *and*
pollutant — that split already existed). This mirrors the project's
existing "one request per partition" pattern (see
`docs/pipelines/eea_measurements.md` for why measurements already did
this per pollutant) and keeps every country's staging/validation/
hash-compare state (see `docs/storage_and_incremental.md`) fully
isolated — a failed or changed fetch for one country never affects
another's.

**TED-specific wrinkle:** TED's query language needs ISO3 country codes
(`buyer-country=DEU`), while the rest of this project uses ISO2. Rather
than switch the whole project to ISO3 (which the other two sources don't
use) or guess a conversion, `ingestion/ted/notices.py` keeps a small
static `EU_ISO2_TO_ISO3` table and converts only when building the query
string — storage paths and the `country_code` column stay ISO2 like
everywhere else. An unmapped code raises immediately rather than being
silently skipped or mis-converted.

## Storage layout

Country is a path segment, immediately under each dataset's existing
base directory, at every layer it applies to:

```
data/raw/eea/stations/<country>/stations_raw.json
data/raw/eea/stations/<country>/state.json
data/normalized/eea/stations/<country>/station_metadata.parquet
data/transformed/eea/stations/<country>/station_metadata.parquet

data/raw/eea/measurements/<country>/<year>/<pollutant>/*.parquet
data/raw/eea/measurements/<country>/manifest.jsonl
data/raw/eea/measurements/<country>/state.json
data/normalized/eea/measurements/<country>/<year>/<pollutant>/*.parquet
data/transformed/eea/measurements/<country>/<year>/<pollutant>/*.parquet

data/raw/ted/<country>/notices.jsonl
data/raw/ted/<country>/state.json
data/normalized/ted/<country>/notices.parquet
data/transformed/ted/<country>/notices.parquet
```

**Not** country-partitioned: TED codelists and NUTS boundaries. Both are
EU-wide reference/lookup data, not scoped to any one country to begin
with (see `docs/pipelines/ted_codelists.md` and
`docs/pipelines/eea_nuts_boundaries.md`) — there's no per-country split
that would mean anything for them, so `countries` doesn't apply there at
all.

## The `country_code` column

Added as an explicit column wherever a *record* (not just a file) is
about one country — measurements and TED notices. In both cases the raw
schema has no country field at all, so it's added once, at
normalization, read from the file's own directory path (which ingestion
already laid out per country) — never guessed from in-file content:

- **EEA measurements** — `normalization.eea.measurements.add_country_from_path()`
  stamps `country_code` from the raw file's `<country>/<year>/<pollutant>/`
  folder.
- **TED notices** — `normalization.ted.notices.flatten_notice()` stamps
  `country_code` from the raw file's `<country>/` folder. This is
  deliberately kept separate from TED's own `buyer-country` field
  (ISO3, e.g. `"DEU"`) — different code space, left untouched.

**EEA stations is the exception:** the raw ArcGIS response already has a
per-row `CountryCode` field (confirmed live), so no extra column is
added on top of it — that field already does the job, and normalization
still doesn't rename raw ArcGIS attribute names (see
`docs/pipelines/eea_stations.md`).

## Data flow

```
run(..., countries=["DE", "PL"])
        │
        ▼
ingestion: one request per country (server-side filtered)
        │
        ▼
data/raw/<source>/<dataset>/<country>/...   (own state.json/manifest per country)
        │
        ▼
normalization: countries auto-discovered from raw/<country>/ subdirectories
        │  + country_code stamped where the raw schema has no country field
        ▼
data/normalized/<source>/<dataset>/<country>/...
        │
        ▼
transformation: countries auto-discovered from normalized/<country>/ subdirectories
        │  (measurements join against the matching country's transformed stations)
        ▼
data/transformed/<source>/<dataset>/<country>/...
```
