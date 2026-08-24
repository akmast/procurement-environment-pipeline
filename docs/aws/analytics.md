# Analytics: Glue, Athena

How the Gold Layer is exposed for SQL/BI querying — a separate,
purely read-only layer on top of `docs/pipelines/gold_layer.md`.
Nothing here writes back into `data/`, runs on a schedule, or
participates in the Step Functions dependency graph in
`architecture.md`.

**No dashboard/BI tool is deployed by this Terraform.** An earlier
version of this stack included a Metabase instance on EC2 (with an
Elastic IP and its own IAM role); it was removed — the always-on,
publicly-reachable EC2 instance was more infrastructure (and cost,
and attack surface) than this project wants to run and maintain for
now. Glue and Athena stay, since they're serverless, essentially
free at this data volume, and useful on their own for ad-hoc SQL
querying (Athena console, CLI, or any BI tool's Athena driver run
elsewhere) without committing to hosting a dashboard server.

## Components

| Component | Role |
|---|---|
| AWS Glue Data Catalog (`glue.tf`) | One database (`procurement_gold` by default), three tables — `eea_measurements`, `ted_notices`, `eurostat_agriculture_accounts` — each pointing at its Gold parquet file's directory (`s3://<data bucket>/data/gold/<source>/`) |
| Amazon Athena (`athena.tf`) | Serverless SQL over the Glue tables; one workgroup (`<project>-gold`), query results written to `s3://<data bucket>/athena-results/` (auto-expired after 7 days, see `s3.tf`) |

Table schemas are defined explicitly in `glue.tf`, not discovered by
a Glue Crawler — every Gold build already produces one fixed,
documented column set (`gold/<source>/*.py`'s `RENAME` dicts), so a
crawler would only add cost and IAM surface for schema inference this
project doesn't need. **If a Gold column ever changes, update the
matching `aws_glue_catalog_table` in the same change** — Athena won't
error on a stale definition, it'll just silently return nulls/wrong
types for the drifted column.

### A column type worth knowing before building charts

`eea_measurements.pollutant_code`, `validity_code`, and
`verification_code` are **numeric** (`bigint` in Glue, `Int64` in the
pipeline — see `normalization/eea/measurements.py`'s `astype`
calls) — EEA's own vocabulary codes, not strings like `"PM10"`. A
chart or query grouping by `pollutant_code` will show numeric codes
unless you join against EEA's pollutant vocabulary or build a
`CASE`/lookup in the Athena query to map codes to human-readable
labels.

## Querying

Anyone with access to `athena_workgroup_name`/`athena_database_name`
(see `outputs.tf`) can query the Gold tables directly — no separate
setup needed:

- **AWS Console**: Athena → Query editor → pick the
  `<project>-gold` workgroup and `procurement_gold` database.
- **AWS CLI**:
  ```
  aws athena start-query-execution \
    --query-string "SELECT * FROM eea_measurements LIMIT 10" \
    --work-group <project>-gold \
    --query-execution-context Database=procurement_gold
  ```
- **A BI tool run elsewhere** (Metabase, QuickSight, a local
  desktop tool, etc.): point its Athena driver at this account/region,
  the `<project>-gold` workgroup, and `procurement_gold` database —
  whatever runs the tool needs AWS credentials with (at minimum)
  Athena query permissions plus read access to the Glue Catalog and
  the Gold S3 prefix.
- **Local Metabase in Docker** (`local/metabase/`): the maintained,
  ready-to-run option for this — a `docker-compose.yml` running
  Metabase against this exact Athena workgroup/Glue database, reading
  AWS credentials from your local AWS CLI/SSO profile (never static
  keys), plus a minimal IAM policy document (`iam-policy.json`) scoped
  to exactly the permissions above. See `local/metabase/README.md` for
  setup, the Athena connection walkthrough, and the full command
  reference.

## Cost

Rough monthly cost, `eu-central-1`, beyond the existing pipeline spend:

| Item | Approx. cost |
|---|---|
| Glue Data Catalog (3 tables) | Free tier covers this easily |
| Athena queries | $5/TB scanned — negligible at this data volume |

No change to `budget.tf`'s monthly limit was needed for this —
still notification-only, never auto-disables anything (see
`operations.md`).
