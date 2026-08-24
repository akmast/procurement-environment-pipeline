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

Kept in this exact order; everything else from the precursor stage is
dropped. `unit`/`aggregation_type`/`time_label` and TED's list-valued
fields (`buyer_country`, `classification_cpv`,
`place_of_performance_country`, `green_procurement_criteria`) and the
other codelist labels are deliberately excluded — not needed for
Gold-level analysis (and unhashable list columns would break the
exact-row deduplication below).

**Eurostat** (`geo` renamed to `nuts2`):

```
country_code, freq, freq_label, am_item, am_item_label,
indic_agr, indic_agr_label, unit_label, nuts2, geo_label, time, value
```

**EEA** (`nuts1_code`/`nuts2_code`/`nuts3_code` renamed to `nuts1`/`nuts2`/`nuts3`):

```
country_code, sampling_point, pollutant, period_start, period_end,
value, unit, validity, verification, result_time, location,
nuts1, nuts2, nuts3
```

**TED** (no renames):

```
country_code, publication_number, publication_date, contract_conclusion_date,
buyer_name, total_value, total_value_currency,
nuts, nuts1, nuts2, nuts3, nuts_label, nuts1_label
```

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
5. Write one file: `data/gold/<source>/<name>.parquet`
   (`agriculture_accounts.parquet`, `measurements.parquet`,
   `notices.parquet`).

Reads/writes go through `common.storage`, so `storage_mode="local"`/
`"cloud"` run identically.

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
