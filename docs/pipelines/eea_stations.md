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

EEA ArcGIS FeatureServer. Service's own self-documenting directory page
(field list, geometry type, capabilities):
https://air.discomap.eea.europa.eu/arcgis/rest/services/AirQuality/AirQualityDownloadServiceEUMonitoringStations/MapServer
— the general `where`/pagination query contract this service follows is
the standard Esri ArcGIS REST API, documented at
https://developers.arcgis.com/rest/services-reference/enterprise/query-feature-service-layer/.

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
3. Writes the whole list to a staging path, reads it back, validates it's
   well-formed JSON, and only then — if it's also different from what's
   already stored (`data/raw/eea/stations/state.json`) — promotes it to
   `data/raw/eea/stations/stations_raw.json`. An unchanged or invalid
   station list is left alone. Reads/writes go through `common.storage`,
   so this runs identically for `storage_mode="local"` and `"cloud"` —
   see `docs/storage_and_incremental.md` for staging/validation/hashing
   and storage modes, shared across pipelines.

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
3. **NUTS enrichment:** loads the NUTS3 boundary polygons fetched by
   `ingestion.eea.nuts_boundaries` (see
   `docs/pipelines/eea_nuts_boundaries.md`) and, for each station, finds
   which NUTS3 polygon contains its `(longitude, latitude)` point —
   using `shapely.strtree.STRtree` as a fast bounding-box pre-filter,
   then an exact `.contains()` test on each candidate. Both coordinates
   and boundaries are in WGS84 (EPSG:4326), so no reprojection is
   needed. Adds three new columns: `nuts3_code` (the matched region,
   e.g. `"DE712"`), and `nuts2_code`/`nuts1_code` derived by slicing
   that code's prefix (`"DE71"`, `"DE7"`) rather than a second spatial
   lookup — NUTS codes nest by construction. A station with a missing
   coordinate, an unmatched coordinate (outside every known polygon), or
   a missing boundaries reference file gets `None` in all three NUTS
   columns instead of failing the run. All existing columns
   (`latitude`, `longitude`, and the rest of the station metadata) are
   kept unchanged.
4. Writes `data/transformed/eea/stations/station_metadata.parquet`.

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
        │  + NUTS enrichment (point-in-polygon match against
        │    data/reference/eea/nuts_boundaries/nuts3_boundaries.geojson)
        ▼
data/transformed/eea/stations/station_metadata.parquet
        (adds nuts1_code / nuts2_code / nuts3_code)
```
