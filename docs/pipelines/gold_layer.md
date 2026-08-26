# Gold Layer

## What it is

The final, analysis-ready layer: **one Parquet file per precursor
partition** — mirroring the precursor stage's own partitioning
(country/year/pollutant for EEA, country only for TED, country/year
for Eurostat) — with only the columns useful for analysis, named and
ordered explicitly. Built by `gold/<source>/*.py`
(`gold/eurostat/agriculture_accounts.py`, `gold/eea/measurements.py`,
`gold/ted/notices.py`). Athena reads every file in a source's Gold
folder as one table, so this is transparent at query time — it just
means a Gold build never has to touch a partition it wasn't given.

A run processes only the partition(s) it's given (normally the exact
partition(s) its precursor stage actually touched this run, via
`--input-manifest` — see "Running it" below) and **overwrites** the
matching Gold file(s) in place. It never appends: reprocessing the
same partition always replaces its Gold file's entire content, so a
row is never duplicated across two different files for the same
partition. This matters especially for TED, since transformation
always rewrites a touched country's *entire* notice history in one
file, not just new notices — overwriting the matching Gold file in
place (rather than writing a new file every run) is what keeps Athena
from ever seeing the same notice twice.

## Where each source reads from

| Source | Reads from | Why |
|---|---|---|
| Eurostat | `normalization.eurostat.agriculture_accounts` output (`data/normalized/eurostat/regional_agricultural_accounts/`) | Eurostat has no transformation stage (see `main.py`'s `FAMILY_STAGES`) |
| EEA | `transformation.eea.measurements` output (`data/transformed/eea/measurements/`) | Station location/NUTS codes are only joined in at transformation |
| TED | `transformation.ted.notices` output (`data/transformed/ted/`) | `nuts_label`/`nuts1_label` only exist after transformation's codelist join |

## Output columns

Kept in this exact order (source column name, and its Gold name if
renamed); everything else from the precursor stage is dropped.
`unit`/`aggregation_type`/`time_label` and TED's list-valued fields
(`buyer_country`, `classification_cpv`, `place_of_performance_country`,
`green_procurement_criteria`) and the other codelist labels are
deliberately excluded — not needed for Gold-level analysis (and
unhashable list columns would break the exact-row deduplication
below). Renames are defined in each module's own `RENAME` dict.

**Eurostat** (`gold/eurostat/agriculture_accounts.py`):

| Source column | Gold column |
|---|---|
| `country_code` | `country_code` |
| `freq` | `frequency_code` |
| `freq_label` | `frequency_label` |
| `am_item` | `agricultural_item_code` |
| `am_item_label` | `agricultural_item_label` |
| `indic_agr` | `agricultural_indicator_code` |
| `indic_agr_label` | `agricultural_indicator_label` |
| `unit_label` | `unit_label` |
| `geo` | `nuts2` |
| `geo_label` | `nuts2_label` |
| `time` | `reference_year` |
| `value` | `indicator_value` |

**EEA** (`gold/eea/measurements.py`):

| Source column | Gold column |
|---|---|
| `country_code` | `country_code` |
| `sampling_point` | `sampling_point_id` |
| `pollutant` | `pollutant_code` |
| `period_start` | `measurement_period_start` |
| `period_end` | `measurement_period_end` |
| `value` | `measurement_value` |
| `unit` | `measurement_unit` |
| `validity` | `validity_code` |
| `verification` | `verification_code` |
| `result_time` | `result_timestamp` |
| `location` | `station_location` |
| `nuts1_code` | `nuts1` |
| `nuts2_code` | `nuts2` |
| `nuts3_code` | `nuts3` |

**TED** (`gold/ted/notices.py`):

| Source column | Gold column |
|---|---|
| `country_code` | `country_code` |
| `publication_number` | `notice_publication_number` |
| `publication_date` | `notice_publication_date` |
| `contract_conclusion_date` | `contract_conclusion_date` |
| `buyer_name` | `buyer_name` |
| `total_value` | `contract_total_value` |
| `total_value_currency` | `contract_currency_code` |
| `nuts` | `place_of_performance_nuts` |
| `nuts1` | `nuts1` |
| `nuts2` | `nuts2` |
| `nuts3` | `nuts3` |
| `nuts_label` | `place_of_performance_nuts_label` |
| `nuts1_label` | `nuts1_label` |

## How a build works

Each precursor partition is processed independently, one at a time
(a failure on one partition is logged and recorded as failed —
`common.manifest.StageResult` — without aborting the rest of the run):

1. Discover (or accept explicit) countries, resolve every matching
   precursor Parquet file (`common.storage.resolve_paths`) — normally
   just the file(s) named in this run's own `--input-manifest`.
2. Read that one precursor file (`common.gold.build_gold_partition`).
   A file missing an expected column is logged and contributes `NaN`
   for it rather than failing the whole build. Select the columns
   above, in that order; apply the renames; drop exact duplicate rows
   within that one file (`DataFrame.drop_duplicates()`).
3. Cast every column to its declared dtype (`common.gold.enforce_dtypes`,
   each module's own `GOLD_DTYPES`) — never left to what pandas/pyarrow
   happen to infer from the precursor file, which can drift file to
   file (see "Dtypes are enforced, not inferred" below).
4. Drop rows missing a required field (`common.gold.drop_missing_required`,
   each module's own `REQUIRED_COLUMNS` — see "Null-guard rules" below).
5. Compute the Gold path that mirrors this precursor file's own
   partition segments (`common.gold.gold_partition_path` — e.g.
   `data/transformed/eea/measurements/DE/2021/PM10/measurements.parquet`
   becomes `data/gold/eea/measurements_DE_2021_PM10.parquet`) and
   **overwrite** it — never append.

Reads/writes go through `common.storage`, so `storage_mode="local"`/
`"cloud"` run identically.

## Dtypes are enforced, not inferred

Athena reads every Parquet file in a source's Gold folder as one
table, so if any one of those files drifted in dtype from the rest (a
stale file written by an older code version, a partition that happens
to be all-null in one column), the table's schema-on-read would break
for every query, not just that partition. That's exactly what happened
in production: `eea_measurements.pollutant_code` was cast to `Int64`
by normalization, but the real Gold Parquet file had it as
`large_string` — one drifted partition was enough — while
`infrastructure/terraform/glue.tf` still declared it `bigint`, so every
Athena query against the table failed with `HIVE_BAD_DATA: Field
pollutant_code's type BINARY ... is incompatible with type bigint`.

Each `gold/<source>/*.py` module declares a `GOLD_DTYPES` dict (column
-> dtype kind) applied via `common.gold.enforce_dtypes()` right after
`build_gold_partition()`, so every partition file's schema is always
exactly what's declared — independent of what that one precursor file,
or any other partition's file, happens to contain. **Every code/label/identifier column is
`string`, even ones that look numeric** — `pollutant_code` included —
since these are categorical values (EEA vocabulary codes, NUTS codes,
...), not arithmetic ones, and can have leading zeros or non-digit
characters. `infrastructure/terraform/glue.tf`'s three tables are kept
in sync column-for-column with each module's `GOLD_DTYPES` — **update
both in the same change** if a Gold column's type ever needs to
change.

## Null-guard rules

Each module also declares `REQUIRED_COLUMNS`, applied via
`common.gold.drop_missing_required()` right after `enforce_dtypes()`:
a row missing any of them is dropped from the Gold file entirely. An
empty or whitespace-only string in a required string column is treated
as missing too, not just an actual `NULL`/`NaN`.

- **EEA**: `country_code`, `pollutant_code`, `measurement_period_start`,
  `measurement_value`, `measurement_unit`, `validity_code` are required
  — an unvalidated measurement isn't meaningful for analysis.
  `verification_code` is deliberately **not** required: an unverified-
  but-validated measurement is still usable.
- **TED**: only `country_code`, `notice_publication_number`,
  `notice_publication_date` are required — the fields that identify a
  notice. `contract_total_value`/`contract_currency_code` are
  deliberately **not** required: a notice with an unknown value still
  counts for `COUNT(DISTINCT notice_publication_number)`-style metrics,
  just not for value aggregation. Any query that sums/averages
  `contract_total_value` must filter `WHERE contract_total_value IS NOT
  NULL AND contract_currency_code IS NOT NULL` itself — Gold Layer does
  not do that filtering, since it would silently break the count use
  case.
- **Eurostat**: every column is required except `frequency_code`/
  `frequency_label` — a row missing any other field isn't usable for
  analysis and there's no Eurostat equivalent of TED's count-only case.

## Running it

**Locally / directly in Python** — `countries` is required, same
"explicit partitions only" convention as every other stage. Each
listed country/partition is (re)written independently; nothing else
under the source's Gold folder is touched:

```python
from gold.eurostat.agriculture_accounts import run, discover_countries
run(storage_mode="local", countries=discover_countries("local"))  # rebuild every partition currently normalized
run(storage_mode="local", countries=["DE", "PL"])                  # just these partitions
```

Same shape for `gold.eea.measurements` and `gold.ted.notices`.
`cleanup_legacy_file=True` additionally deletes the source's old
single combined Gold file (`data/gold/<source>/<name>.parquet`, from
before this per-partition model), if one is still present — see
"Migrating from the old combined-file model" below.

**Via `main.py`** (the CLI Step Functions' ECS tasks actually run) —
either `--countries`/`--paths` for specific partitions, or `--discover`
for every partition currently on disk (and legacy-file cleanup):

```
python main.py stage --source eurostat-agriculture-accounts --stage gold --countries DE PL --storage-mode cloud
python main.py stage --source eea-measurements --stage gold --discover --storage-mode cloud
```

**In AWS — automatically, inside `HistoricalStateMachine`/`UpdateStateMachine`.**
Each of the three source branches (see `docs/aws/architecture.md`'s
dependency graph) runs its own Gold build itself, right after its last
data stage — `EeaRunTransformation`/`TedRunTransformation` for EEA/TED,
`EurostatRunNormalization` for Eurostat (no transformation stage there,
so Gold reads straight from normalization, per the table above) — but
**only if that stage actually wrote something new this run**, and even
then **only for the partition(s) that stage actually touched**:

```
... last data stage ...
        │
        ▼
CheckHasNewData  (main.py check-manifest-has-output --run-id ... --source ... --stage ...)
        │
   ┌────┴────┐
 nothing    wrote
  new      something
   │           │
   ▼           ▼
 skip      RunGold  (main.py stage --source ... --stage gold --storage-mode cloud
   │            --input-manifest s3://.../runs/<run_id>/<source>/<last-stage>.json)
   │           │
   └─────┬─────┘
         ▼
     <source>Succeeded
```

`check-manifest-has-output` reads that stage's own manifest
(`runs/<run_id>/<source>/<stage>.json`) and exits non-zero if
`written_paths` is empty — a genuinely unremarkable outcome (e.g. a
refresh that found nothing changed upstream), not an error. The ASL
`Catch`es that non-zero exit straight to the branch's `Succeeded`
state, skipping the Gold build entirely — so no wasted ECS task run.
`RunGold` itself reads `--input-manifest` (the same file
`CheckHasNewData` just confirmed is non-empty) to get the exact list
of precursor paths this run touched — exactly the same mechanism
`normalization`/`transformation` already use for their own
`--input-manifest` wiring, not a Gold-specific one. Gold's own
`written_paths` land in `runs/<run_id>/<source>/gold.json`. A real
Gold build failure still fails the branch (and the overall
historical/update run) the same as any other stage.

This means Gold always reflects the latest transformed/normalized data
for exactly the partition(s) that changed, after any historical or
update run that changed something — nothing extra to run yourself,
and no already-Gold'd partition gets rewritten (or re-read) for no
reason.

**In AWS — manually, via `GoldStandardStateMachine`** (see
`infrastructure/terraform/templates/gold_standard.asl.json.tpl`): a
separate, supplementary state machine for an **unconditional** full
rebuild of every partition of all three sources, regardless of whether
anything changed recently — useful e.g. right after fixing a bug in
the Gold build logic itself, without needing to re-run
historical/update just to trigger a rebuild. This is also the state
machine that performs legacy-file cleanup (see below). Manual only,
never on a schedule.

Start it via **Actions → Run Pipeline → Run workflow**, `state_machine: gold-standard`
(`sources`/`countries`/`from_year`/`to_year`/`run_id`/`start_stage` are
all ignored for this one), or directly:

```
aws stepfunctions start-execution \
  --state-machine-arn <GoldStandardStateMachine ARN, see `terraform output gold_standard_state_machine_arn`> \
  --input '{}'
```

The external input is deliberately empty — `{}` — there is nothing to
select; every build always rebuilds (`--discover`) every partition
currently available for its source.

## Migrating from the old combined-file model

Before this per-partition model, each source wrote one combined file:
`data/gold/<source>/<name>.parquet` (`agriculture_accounts.parquet`,
`measurements.parquet`, `notices.parquet`). Athena reads every file
under a source's Gold folder as one table, so if that old combined
file is still present alongside the new per-partition files, every row
it contains would be double-counted against the same row now also
living in a partition file.

`gold/<source>/*.py`'s `run(..., cleanup_legacy_file=True)` deletes
that old combined file, if still present, before writing anything —
but only when called with `--discover` (i.e. only from
`GoldStandardStateMachine`; an ordinary `historical`/`update` run
always passes `cleanup_legacy_file=False` and leaves it alone, since
it only ever touches the partition(s) that changed and has no reason
to assume every other partition has already been migrated). **Run
`GoldStandardStateMachine` once after deploying this change** to fully
retire each source's old combined file — until that runs, the old file
stays in place and Athena queries against that source's table will
double-count every row it contains.
