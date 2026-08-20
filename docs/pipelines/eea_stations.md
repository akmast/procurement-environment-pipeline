# EEA station metadata

## What this pipeline gets

Metadata about EEA air quality monitoring stations: station code, name,
country, coordinates, and a few technical attributes (station class,
etc). One row per station. This is reference/dimension data — it is later
joined with `eea_measurements` on the station code to know *where* a
measurement was taken.

There is no `country` filter applied post-download anywhere in this
pipeline — the country scope is applied once, server-side, at the
ingestion request itself (see below).

## Source

EEA ArcGIS FeatureServer:

```
Method: GET
URL:    https://air.discomap.eea.europa.eu/arcgis/rest/services/AirQuality/
        AirQualityDownloadServiceEUMonitoringStations/MapServer/0/query

Query parameters:
  where            "CountryCode='DE'"   — server-side country filter
  outFields        "*"                  — return all attribute columns
  returnGeometry   "true"
  outSR            "4326"               — WGS84 lat/lon
  f                "json"
  resultOffset     0, 2000, 4000, ...   — pagination cursor
  resultRecordCount 2000                — page size

Expected response:
  JSON with a "features" array. Each feature looks like:
  {
    "attributes": { "OBJECTID": ..., "AirQualityStation": ...,
                     "CountryCode": "DE", "AirQualityStationEoICode": ...,
                     "AQStationName": ..., "stationClass": ...,
                     "PopupInfo": "<html>...</html>", ... },
    "geometry": { "x": <longitude>, "y": <latitude> }
  }
  Plus a top-level "exceededTransferLimit": bool flag used for pagination.
```

`where` is standard ArcGIS REST attribute-filter syntax — this is a real
server-side filter (the ArcGIS service only returns matching features),
not something we filter after downloading. `CountryCode` is a confirmed
field on this layer (seen in a live response on 2026-08-14).

### Pagination

The service returns at most `resultRecordCount` features per call. We
keep requesting with an increasing `resultOffset` until a page comes back
with `exceededTransferLimit: false` and fewer features than the page
size (or an empty page) — that's the signal there's nothing left.

```
Request 1 → offset=0    → up to 2000 features
Request 2 → offset=2000 → up to 2000 features
Request 3 → offset=4000 → fewer than 2000 features, exceededTransferLimit=false
                              │
                              ▼
                     all features combined
```

## Ingestion — `ingestion/eea/stations.py`

1. Pages through the endpoint above (server-side filtered to `COUNTRY`,
   currently `"DE"`).
2. Concatenates every page's `features` list into one list — this is the
   raw API shape, untouched (each entry still has its own `attributes` +
   `geometry`).
3. Writes the whole list as one JSON file:
   `data/raw/eea/stations/stations_raw.json` — only if its content hash
   differs from what's already stored (`data/raw/eea/stations/state.json`);
   an unchanged station list is left alone. Reads/writes go through
   `common.storage`, so this runs identically for `storage_mode="local"`
   and `"cloud"` — see `docs/storage_and_incremental.md` for both of
   those, they're shared across pipelines.

Ingestion does **not** flatten geometry into columns, does not drop any
field (including the large `PopupInfo` HTML blob), and does not
deduplicate. That's normalization's job.

## Normalization — `normalization/eea/stations.py`

1. Loads `stations_raw.json`.
2. Flattens each feature into one row: `attributes` fields become
   columns, plus `longitude`/`latitude` pulled out of `geometry`.
3. Drops `PopupInfo` — it's decorative HTML for the EEA web map popup,
   not analytical data.
4. Writes `data/normalized/eea/stations/station_metadata.parquet`.

Normalization only reshapes raw data into a readable table — it does not
decide which rows are duplicates. That's a heavier, more opinionated
decision and belongs to transformation instead.

## Transformation — `transformation/eea/stations.py`

1. Loads the normalized table.
2. Deduplicates by `AirQualityStationEoICode` (the station's EoI code —
   the join key used against measurements).
3. Writes `data/transformed/eea/stations/station_metadata.parquet`.

## Data flow

```
ArcGIS FeatureServer (paged, country-filtered server-side)
        │
        ▼
ingestion.eea.stations.run(mode="stations")
        │  raw features, as returned, no reshaping
        ▼
data/raw/eea/stations/stations_raw.json
        │
        ▼
normalization.eea.stations.run()
        │  flatten → drop PopupInfo (no dedup)
        ▼
data/normalized/eea/stations/station_metadata.parquet
        │
        ▼
transformation.eea.stations.run()
        │  dedup by station code
        ▼
data/transformed/eea/stations/station_metadata.parquet
```
