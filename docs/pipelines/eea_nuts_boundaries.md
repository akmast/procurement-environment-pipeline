# NUTS region boundaries (reference data)

## What this pipeline gets

The official geographic boundaries (polygons) of NUTS3 regions — the
finest level of the EU's NUTS (Nomenclature of Territorial Units for
Statistics) classification. This is EU-wide reference/lookup data, not
scoped to any country and not a fact about air quality or procurement —
hence `data/reference/`, not `data/raw/`.

It exists for one purpose: `transformation.eea.stations` uses these
polygons to figure out which NUTS1/NUTS2/NUTS3 region each air quality
station's coordinates fall inside.

## Why NUTS, and why this method

TED procurement notices already use NUTS3 codes (e.g. `DE712`) for place
of performance (see `docs/pipelines/ted_codelists.md`, the `nuts`
codelist). Tagging EEA stations with the same NUTS codes, derived from
their coordinates, lets the two datasets be joined/compared on a common
regional key later — that's the whole motivation for this pipeline.

NUTS codes nest by construction: a NUTS3 code's own characters are its
NUTS1/NUTS2 codes too (`DE712` → NUTS2 `DE71` → NUTS1 `DE7`). So only
NUTS3 boundaries need to be downloaded and spatially matched — NUTS1 and
NUTS2 codes for a station are derived by slicing the matched NUTS3 code,
not by a second spatial lookup.

## Source

GISCO — Eurostat's official geodata distribution service:
https://gisco-services.ec.europa.eu/distribution/v2/nuts/

```
Method: GET
URL:    https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/
        NUTS_RG_10M_2021_4326_LEVL_3.geojson

Expected response:
GeoJSON FeatureCollection. One feature per NUTS3 region:
{
  "type": "Feature",
  "properties": { "NUTS_ID": "DE712", ... },
  "geometry": { "type": "Polygon" | "MultiPolygon", "coordinates": [...] }
}
```

`4326` in the filename is the coordinate reference system — WGS84
latitude/longitude, the same system EEA station coordinates are already
published in (`outSR=4326`, see `docs/pipelines/eea_stations.md`), so no
reprojection is needed before matching. `LEVL_3` selects NUTS3-only
boundaries. `2021` is the NUTS classification version; `10M` is
simplification resolution (1:10 million — plenty precise for a
point-in-polygon station match, and much smaller than the full-detail
files).

**Open item:** this exact URL follows GISCO's documented file naming
convention but has not been live-verified — outbound access to
`gisco-services.ec.europa.eu` is blocked in the development sandbox.
Confirm on the first real run; if it 404s, check the available
files/years at the distribution page above.

## Ingestion — `ingestion/eea/nuts_boundaries.py`

Downloads the GeoJSON file, stages it, validates it's well-formed
GeoJSON (a JSON `FeatureCollection` with a `features` list —
`common.validation.is_valid_geojson`), and only then — if it's also
different from what's already stored (`.../state.json`) — writes it
byte-for-byte to `data/reference/eea/nuts_boundaries/nuts3_boundaries.geojson`.
NUTS boundaries change only when Eurostat publishes a new classification
version (every few years), so most runs should find nothing changed.
Reads/writes go through `common.storage`
(`storage_mode="local"`/`"cloud"`) — see `docs/storage_and_incremental.md`
for the full staging/validation/hashing flow, shared across pipelines.

## Consumer — `transformation.eea.stations`

Loads the GeoJSON, builds a `shapely.strtree.STRtree` spatial index over
the NUTS3 polygons, and for each station tests whether its
`(longitude, latitude)` point falls inside one of them. See
`docs/pipelines/eea_stations.md` for the matching logic and how
`nuts1_code`/`nuts2_code`/`nuts3_code` end up in the transformed station
table.

## Data flow

```
GET gisco-services.ec.europa.eu/.../NUTS_RG_10M_2021_4326_LEVL_3.geojson
        │
        ▼
data/reference/eea/nuts_boundaries/nuts3_boundaries.geojson   (raw GeoJSON, untouched)
        │
        ▼
transformation.eea.stations: point-in-polygon match against station coordinates
        │
        ▼
nuts1_code / nuts2_code / nuts3_code columns in
data/transformed/eea/stations/station_metadata.parquet
```
