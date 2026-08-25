# Gold Layer

## What it is

The final, analysis-ready layer: one Parquet file per source — no
split by country, year, or pollutant — with only the columns useful
for analysis, named and ordered explicitly. Built by `gold/<source>/*.py`
(`gold/eurostat/agriculture_accounts.py`, `gold/eea/measurements.py`,
`gold/ted/notices.py`), each combining every partition of its
precursor stage's output currently on disk.

This is a full rebuild every run, not an incremental append: there's
no Gold-level partitioning to merge into, so a Gold build always
reflects exactly the countries it was given (normally *every* country
currently normalized/transformed — see "Running it" below).

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

1. Discover (or accept explicit) countries, resolve every matching
   Parquet file under the precursor stage's base directory
   (`common.storage.resolve_paths`).
2. Read and concatenate all of them (`common.gold.build_gold_table`).
   A file missing an expected column is logged and contributes `NaN`
   for it rather than failing the whole build.
3. Select the columns above, in that order; apply the renames.
4. Drop exact duplicate rows (`DataFrame.drop_duplicates()` —
   deterministic: `resolve_paths`/`list_files` return sorted paths, so
   the first occurrence kept is always the same one).
5. Cast every column to its declared dtype (`common.gold.enforce_dtypes`,
   each module's own `GOLD_DTYPES`) — never left to what pandas/pyarrow
   happen to infer from concatenating partition files, which can drift
   per-file (see "Dtypes are enforced, not inferred" below).
6. Drop rows missing a required field (`common.gold.drop_missing_required`,
   each module's own `REQUIRED_COLUMNS` — see "Null-guard rules" below).
7. Write one file: `data/gold/<source>/<name>.parquet`
   (`agriculture_accounts.parquet`, `measurements.parquet`,
   `notices.parquet`).

Reads/writes go through `common.storage`, so `storage_mode="local"`/
`"cloud"` run identically.

## Dtypes are enforced, not inferred

A Gold build concatenates every partition file it finds — if one of
them drifted in dtype (a stale file written by an older code version,
a partition that happens to be all-null in one column), pandas'
concatenation can silently widen the *whole* combined column to
whatever type covers the mix (typically `object`). That's exactly what
happened in production: `eea_measurements.pollutant_code` was cast to
`Int64` by normalization, but the real Gold Parquet file had it as
`large_string` — one drifted partition was enough — while
`infrastructure/terraform/glue.tf` still declared it `bigint`, so every
Athena query against the table failed with `HIVE_BAD_DATA: Field
pollutant_code's type BINARY ... is incompatible with type bigint`.

Each `gold/<source>/*.py` module now declares a `GOLD_DTYPES` dict
(column -> dtype kind) applied via `common.gold.enforce_dtypes()` right
after `build_gold_table()`, so the written Parquet file's schema is
always exactly what's declared, regardless of what any individual
partition file contained. **Every code/label/identifier column is
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
"explicit partitions only" convention as every other stage:

```python
from gold.eurostat.agriculture_accounts import run, discover_countries
run(storage_mode="local", countries=discover_countries("local"))  # everything currently normalized
run(storage_mode="local", countries=["DE", "PL"])                  # just these
```

Same shape for `gold.eea.measurements` and `gold.ted.notices`.

**Via `main.py`** (the CLI Step Functions' ECS tasks actually run):

```
python main.py stage --source eurostat-agriculture-accounts --stage gold --discover --storage-mode cloud
python main.py stage --source eea-measurements --stage gold --discover --storage-mode cloud
python main.py stage --source ted-notices --stage gold --discover --storage-mode cloud
```

**In AWS — automatically, inside `HistoricalStateMachine`/`UpdateStateMachine`.**
Each of the three source branches (see `docs/aws/architecture.md`'s
dependency graph) runs its own Gold build itself, right after its last
data stage — `EeaRunTransformation`/`TedRunTransformation` for EEA/TED,
`EurostatRunNormalization` for Eurostat (no transformation stage there,
so Gold reads straight from normalization, per the table above) — but
**only if that stage actually wrote something new this run**:

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
 skip      RunGold  (main.py stage --source ... --stage gold --discover ...)
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
state, skipping the Gold rebuild entirely — so no new (identical)
Gold file gets written for no reason, and no wasted ECS task runs. A
real Gold build failure, on the other hand, still fails the branch
(and the overall historical/update run) the same as any other stage.

This means Gold always reflects the latest transformed/normalized data
after any historical or update run that changed something — nothing
extra to run yourself.

**In AWS — manually, via `GoldStandardStateMachine`** (see
`infrastructure/terraform/templates/gold_standard.asl.json.tpl`): a
separate, supplementary state machine for an **unconditional** full
rebuild of all three sources, regardless of whether anything changed
recently — useful e.g. right after fixing a bug in the Gold build
logic itself, without needing to re-run historical/update just to
trigger a rebuild. Manual only, never on a schedule.

Start it via **Actions → Run Pipeline → Run workflow**, `state_machine: gold-standard`
(`sources`/`countries`/`from_year`/`to_year`/`run_id`/`start_stage` are
all ignored for this one), or directly:

```
aws stepfunctions start-execution \
  --state-machine-arn <GoldStandardStateMachine ARN, see `terraform output gold_standard_state_machine_arn`> \
  --input '{}'
```

The external input is deliberately empty — `{}` — there is nothing to
select; every build always discovers and combines everything currently
available for its source.
