# EEA air quality measurements

## What this pipeline gets

Daily air quality measurement values (PM10, PM2.5, NO2, O3, SO2) per
station, as Parquet files, for one or more countries and a date range.
This is the fact table — one row per station/pollutant/day (roughly),
joined later with station metadata (`eea_stations`) on the station code.
Supports multiple countries in one run (`countries=["DE", "PL"]`) — see
`docs/pipelines/countries.md` for the mechanics shared across pipelines.

## Source

EEA air quality downloads API (an Azure-hosted service, documented at
https://eeadmz1-downloads-api-appservice.azurewebsites.net/swagger/index.html).
This is the same API the community `airbase` Python client wraps.

### Step 1 — ask which files exist

One request **per pollutant** — not one request covering all five. This
way we already know which pollutant a file belongs to from the request
that produced it, instead of relying on the numeric `Pollutant` code
inside the file itself (see "Parquet schema" below) or merging by that
code later in normalization.

```
Method: POST
URL:    https://eeadmz1-downloads-api-appservice.azurewebsites.net/ParquetFile/urls

JSON body (one call per pollutant in POLLUTANTS):
{
    "countries": ["DE"],
    "cities": [],
    "pollutants": ["PM10"],
    "dataset": 1,
    "dateTimeStart": "2025-01-01",
    "dateTimeEnd": "2025-01-31",
    "aggregationType": "day",
    "source": "API"
}

Expected response:
Confirmed via a live call (2026-08-19): a plain text body, one URL per
line (not JSON) — `get_file_urls()`'s JSON branch is defensive for a
shape that hasn't actually been observed yet. This response contains no
measurement data yet, only file locations.
```

`countries` is a real request field of this API (confirmed via the
`ParquetDataJSON` type used by the `airbase` client) — this is a
server-side filter, not something we narrow down after downloading.

### Step 2 — download each file

```
Method: GET
URL:    <one of the URLs from step 1>

Expected response:
Parquet file bytes, exactly as published by EEA.
```

```
POST /ParquetFile/urls
        │
        ▼
JSON list of file URLs   (metadata only — no data yet)
        │
        ▼
GET each URL
        │
        ▼
Parquet file bytes
        │
        ▼
saved to Bronze, untouched
```

## Ingestion — `ingestion/eea/measurements.py`

For each requested country, downloads every file returned by step 1
(itself looped per pollutant) and writes it to storage exactly as
received — no parsing, no column selection, no filtering, no dedup. Raw
storage is partitioned by country, year, **and** pollutant, so both are
already known from the path, no lookup needed:

```
data/raw/eea/measurements/<country>/<year>/<pollutant>/<original_filename>.parquet
data/raw/eea/measurements/<country>/manifest.jsonl   — one line per file
                                              actually written (url,
                                              country, year, pollutant,
                                              downloaded_at, size_bytes,
                                              storage_path)
data/raw/eea/measurements/<country>/state.json       — content hash per
                                              file, used to skip
                                              rewriting files whose bytes
                                              haven't changed
```

`<pollutant>` is exactly the string we requested (`"PM10"`, `"PM2.5"`,
`"NO2"`, `"O3"`, `"SO2"`) — reliable by construction, since it's our own
request parameter, not something decoded from the response.

Reads/writes go through `common.storage` (`storage_mode="local"` or
`"cloud"`) and every downloaded file goes through staging → validation
(must actually parse as Parquet) → content-hash compare before it's
allowed to reach `data/raw/...` — a file is only (re)written if it's
both valid *and* changed. See `docs/storage_and_incremental.md` for the
full staging/validation/hashing flow and storage modes, shared across
pipelines, not specific to this one.

Three modes, each looped over `countries`:

- **test** — last 5 days, PM10 only, writes to
  `data/raw/eea/test/<country>/` (doesn't touch the real dataset,
  manifest, or state).
- **historical** — loops over `from_year..to_year` × `POLLUTANTS` for
  each country, one API call + one set of downloads per country/year/
  pollutant combination. For an explicit backfill of years outside the
  automatic refresh window.
- **refresh** — re-checks every year in the current EEA reporting
  "mutable window" (`common.reporting_window.mutable_years()` — current
  year, plus the previous year until its own reporting deadline; see
  `docs/storage_and_incremental.md`) × `POLLUTANTS`, for each country. No
  year is hardcoded. Each file is only rewritten if its content actually
  changed.

## Parquet schema (confirmed via a live file, 2026-08-19)

```
Samplingpoint    e.g. "SPO.DE_DENW105_PM1_dataGroup2"
Pollutant        numeric code, NOT the "PM10"-style name from the
                 request — e.g. 5. Not needed to identify the pollutant:
                 that's already known from the folder the file was
                 downloaded into (see "Ingestion" above). Kept in the
                 normalized schema as `pollutant_code` for reference,
                 but nothing merges on it.
Start, End       measurement window (the two columns run_test() already
                 reads for its logged time range)
Value            float
Unit             e.g. "ug.m-3"
AggType          e.g. "day"
Validity
Verification
ResultTime
DataCapture       confirmed always null across every observed file
                  (2026-08-19) — dropped in normalization, no
                  information to carry forward.
FkObservationLog  internal EEA housekeeping reference (name confirmed
                  live 2026-08-19 — earlier docs guessed
                  "FkObservationId", that was wrong). Dropped in
                  normalization — not useful at this stage.
```

## Normalization — `normalization/eea/measurements.py`

For each country (explicit, or every country found under
`data/raw/eea/measurements/`), reads every raw Parquet file for that
country (via `common.storage.list_files`, so this works the same in
`local`/`cloud`) and writes one normalized file per input file,
mirroring the same `<country>/<year>/<pollutant>/` layout under
`data/normalized/eea/measurements/`. Renames columns to snake_case
(explicit map for the confirmed columns above; anything unrecognized is
auto-converted and logged, never silently guessed), drops `data_capture`
and `fk_observation_log` (see "Parquet schema" above for why), casts `value` from
its raw `Decimal` form to `float` and the date-ish columns to
`datetime64`, and adds two columns read from the raw file's own folder
path — never from a merge or guess: `pollutant` (never from
`pollutant_code`, the renamed raw `Pollutant` column, kept for reference
only) and `country_code` (the raw schema has no country field at all —
this is the only place it's known, since it's ingestion's own per-country
request that produced the folder).

## Transformation — `transformation/eea/measurements.py`

For each normalized measurements file (explicit `countries`, or every
country found under `data/normalized/eea/measurements/`), via
`common.storage.list_files`, one input file → one output file, same
`<country>/<year>/<pollutant>/` layout:

1. **Column selection** — keeps only `country_code`, `sampling_point`,
   `pollutant`, `period_start`, `period_end`, `value`, `unit`,
   `aggregation_type`, `validity`, `verification`, `result_time`.
   `pollutant` (the human-readable name added from the folder path in
   normalization) is kept — `pollutant_code` (the raw numeric EEA code)
   is dropped here, its usefulness ends at normalization.
2. **Station join** — extracts the station's EoI code from
   `sampling_point` (e.g. `"SPO.DE_DEBE034_PM1_dataGroup2"` →
   `"DEBE034"`, its second underscore-separated segment — confirmed
   against real EEA sampling point values) and left-joins against
   `data/transformed/eea/stations/<country>/station_metadata.parquet`
   (the *same* country as the measurement file — read from its own path,
   see `docs/pipelines/countries.md`) on `AirQualityStationEoICode`,
   bringing in `location` (the station's name), `nuts1_code`,
   `nuts2_code`, `nuts3_code`. Each country's station table is loaded
   once and reused for all of its measurement files. NUTS codes are
   never recomputed here — they're a station-level property already
   derived once in `transformation.eea.stations` (see
   `docs/pipelines/eea_stations.md`). A measurement whose station code
   doesn't match any known station keeps its row with these four fields
   left empty, rather than being dropped or crashing the run.
3. Writes `data/transformed/eea/measurements/<country>/<year>/<pollutant>/*.parquet`.

## Data flow

```
POST /ParquetFile/urls (countries=[<one country>], date range, pollutants=[<one pollutant>])
        │  one call per country × pollutant
        ▼
list of Parquet file URLs (for that country/pollutant)
        │
        ▼
GET each URL → Parquet bytes
        │
        ▼
write to staging, read back, validate as Parquet
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
       skip     data/raw/eea/measurements/<country>/<year>/<pollutant>/*.parquet (written, untouched otherwise)
              │
              ▼
        normalization.eea.measurements.run()
              │  rename, drop unused, add pollutant + country_code from path, cast types
              ▼
        data/normalized/eea/measurements/<country>/<year>/<pollutant>/*.parquet
              │
              ▼
        transformation.eea.measurements.run()
              │  select columns + join same-country station location/NUTS codes
              │  (station code extracted from sampling_point)
              ▼
        data/transformed/eea/measurements/<country>/<year>/<pollutant>/*.parquet
```

See `docs/pipelines/countries.md` for the `countries` parameter's shared
mechanics across all pipelines.
