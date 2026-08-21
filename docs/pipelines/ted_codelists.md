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
table (`parse_genericode()`) and writes
`data/normalized/ted/codelists/<id>.parquet` — Parquet rather than JSON,
matching every other normalized dataset in this project and directly
loadable by `pandas.read_parquet` for joins. Each row keeps every column
Genericode provides for that code: `code` itself, a generic `Name`
column, and one `<lang>_label` column per language (confirmed live:
`bul_label`, `spa_label`, ..., `deu_label`, `eng_label`, ...).
Normalization doesn't pick a single label — which one to prefer is an
opinionated choice, left to whatever joins against it.

`codelist_ids` must be passed explicitly (e.g. `["country", "cpv"]`) —
`run()` never defaults to processing every downloaded codelist. Pass
`discover_codelist_ids(storage_mode)` to process everything currently
downloaded — same "explicit partitions only" convention as every other
normalization/transformation module, see `docs/pipelines/countries.md`.

## Consumer — `transformation.ted.notices`

Joins `code` against the coded columns in normalized TED notices
(`notice_type`, `buyer_country`, `total_value_currency`,
`winner_selection_status`, `non_award_justification`, `nuts`, and each
code inside `classification_cpv`) to add human-readable `..._label`
columns, preferring `deu_label` → `eng_label` → `Name` → the code
itself. See `docs/pipelines/ted_notices.md` for the full join. This is
the reason codelists get their own normalization stage at all — they
exist to make that join possible; there's no separate transformation
stage for codelists themselves; they're static lookup tables with
nothing to dedup or enrich on their own.

## Data flow

```
GET raw.githubusercontent.com/.../<codelist>.gc
        │
        ▼
data/reference/ted/codelists/<id>.gc.xml   (raw XML, untouched)
        │
        ▼
normalization: parse Genericode → flat table
        │
        ▼
data/normalized/ted/codelists/<id>.parquet
        │
        ▼
transformation.ted.notices: code -> label join
        │
        ▼
data/transformed/ted/<country>/notices.parquet
        (adds notice_type_label, buyer_country_label, ..., classification_cpv_labels)
```
