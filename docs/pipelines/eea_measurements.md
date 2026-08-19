# EEA air quality measurements

## What this pipeline gets

Daily air quality measurement values (PM10, PM2.5, NO2, O3, SO2) per
station, as Parquet files, for a given country and date range. This is
the fact table — one row per station/pollutant/day (roughly), joined
later with station metadata (`eea_stations`) on the station code.

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

Downloads every file returned by step 1 and writes it to disk exactly as
received — no parsing, no column selection, no filtering, no dedup. Raw
storage is partitioned by year **and pollutant**, so the pollutant is
already known from the path, no lookup needed:

```
data/raw/eea/measurements/<year>/<pollutant>/<original_filename>.parquet
data/raw/eea/measurements/manifest.jsonl   — one line per downloaded file
                                              (url, year, pollutant,
                                              downloaded_at, size_bytes,
                                              local_path)
```

`<pollutant>` is exactly the string we requested (`"PM10"`, `"PM2.5"`,
`"NO2"`, `"O3"`, `"SO2"`) — reliable by construction, since it's our own
request parameter, not something decoded from the response.

Three modes:

- **test** — last 5 days, PM10 only, writes to `data/raw/eea/test/`
  (doesn't touch the real dataset or manifest).
- **historical** — loops over `from_year..to_year` × `POLLUTANTS`, one
  API call + one set of downloads per year/pollutant pair.
- **refresh_current** — deletes the current year's files and manifest
  entries, then re-downloads the current year (all pollutants) from
  scratch.

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
DataCapture
FkObservationId  (or similarly-named id column — truncated in the sample seen)
```

## Normalization — `normalization/eea/measurements.py`

Not implemented yet — schema is being discussed with the project owner
before writing this module (see chat), specifically: renamed/typed
column list, how `Value`/`Unit` are handled, and whether normalized
output stays partitioned like raw or gets combined into one dataset.
Pollutant identity comes from the raw file's folder path, not from a
merge on the numeric `Pollutant` code.

## Data flow

```
POST /ParquetFile/urls (countries=["DE"], date range, pollutants=[<one pollutant>])
        │  one call per pollutant in POLLUTANTS
        ▼
list of Parquet file URLs (for that pollutant)
        │
        ▼
GET each URL → Parquet bytes
        │
        ▼
data/raw/eea/measurements/<year>/<pollutant>/*.parquet   (untouched)
```
