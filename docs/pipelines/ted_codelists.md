# TED reference codelists

## What this pipeline gets

Lookup tables that decode the short codes TED notices use — e.g.
`buyer-country=DEU` → "Germany", `total-value-cur=EUR` → "Euro". These
are EU-wide reference tables, not scoped to any country, and not
procurement facts — hence `data/reference/`, not `data/raw/`.

## Source

GitHub repository `OP-TED/eForms-SDK` (official Publications Office of
the EU repo): https://github.com/OP-TED/eForms-SDK/tree/main/codelists.
Each codelist is one Genericode XML file (`.gc`).

```
Method: GET
URL:    https://raw.githubusercontent.com/OP-TED/eForms-SDK/main/codelists/<filename>.gc

Expected response:
Genericode XML. Structure (relevant parts only):

<gc:CodeList xmlns:gc="...">
  <ColumnSet>
    <Column Id="code"/>
    <Column Id="label"/>
    ...
  </ColumnSet>
  <SimpleCodeList>
    <Row>
      <Value><SimpleValue>DEU</SimpleValue></Value>
      <Value><SimpleValue>Germany</SimpleValue></Value>
    </Row>
    ...
  </SimpleCodeList>
</gc:CodeList>
```

Quirk worth knowing: only the root `<gc:CodeList>` element carries the
`gc:` namespace prefix — `ColumnSet`, `Column`, `Row`, `Value` are all
unprefixed, even though they're nested inside a `gc:`-namespaced root.

We currently fetch 7 codelists (see `CODELISTS` in
`ingestion/ted/codelists.py`): `notice-type`, `winner-selection-status`,
`non-award-justification`, `country`, `currency`, `cpv`, `nuts`.

**Open item:** the `nuts` filename (`nuts.gc`) is a best guess by naming
convention, not confirmed like the others — if the download 404s, that's
expected, and we'll need to find the real filename in the GitHub folder.

## Ingestion — `ingestion/ted/codelists.py`

For each codelist ID, downloads the XML, stages it, validates it's
well-formed XML, and only then — if it's also different from what's
already stored (`.../state.json`) — writes it byte-for-byte to
`data/reference/ted/codelists/<id>.gc.xml`. GitHub rarely changes these,
so most runs write nothing. Reads/writes go through `common.storage`
(`storage_mode="local"`/`"cloud"`) — see `docs/storage_and_incremental.md`
for the full staging/validation/hashing flow. No semantic parsing
happens here (only structural well-formedness) — that's normalization's
job; a failed download (bad filename, 404) is logged and skipped, not
retried with a guessed alternative.

## Normalization — `normalization/ted/codelists.py`

For each raw `.gc.xml` file, parses the Genericode structure into a flat
list of `{column: value}` rows (`parse_genericode()`) and writes
`data/normalized/ted/codelists/<id>.json`.

## Data flow

```
GET raw.githubusercontent.com/.../<codelist>.gc
        │
        ▼
data/reference/ted/codelists/<id>.gc.xml   (raw XML, untouched)
        │
        ▼
normalization: parse Genericode → flat rows
        │
        ▼
data/normalized/ted/codelists/<id>.json
```
