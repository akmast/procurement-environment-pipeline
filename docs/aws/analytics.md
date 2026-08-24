# Analytics: Glue, Athena, Metabase

How the dashboard reads the Gold Layer — a separate, purely
read-only layer on top of `docs/pipelines/gold_layer.md`. Nothing
here writes back into `data/`, runs on a schedule, or participates in
the Step Functions dependency graph in `architecture.md`.

## Components

| Component | Role |
|---|---|
| AWS Glue Data Catalog (`glue.tf`) | One database (`procurement_gold` by default), three tables — `eea_measurements`, `ted_notices`, `eurostat_agriculture_accounts` — each pointing at its Gold parquet file's directory (`s3://<data bucket>/data/gold/<source>/`) |
| Amazon Athena (`athena.tf`) | Serverless SQL over the Glue tables; one workgroup (`<project>-gold`), query results written to `s3://<data bucket>/athena-results/` (auto-expired after 7 days, see `s3.tf`) |
| Metabase (`metabase.tf`) | Dashboard/BI tool — single EC2 instance running Metabase in Docker, connects to Athena as its data source |

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
chart grouping by `pollutant_code` will show numeric codes as its
series names unless you join against EEA's pollutant vocabulary or
build a `CASE`/lookup in the Athena query (or a Metabase custom
column) to map codes to human-readable labels.

## Setting up Metabase → Athena

1. Get the instance reachable: `terraform output metabase_url`, and
   confirm your IP is in `metabase_allowed_cidr_blocks` (see
   `terraform.tfvars.example` — required, no default; re-apply
   whenever your IP changes).
2. First load of that URL is Metabase's own setup wizard (create an
   admin account — this is local to the instance, unrelated to AWS
   IAM).
3. Add a database: **Amazon Athena** (built into Metabase's driver
   list since Metabase 0.40 — no plugin to install).
   - **Region**: `terraform output` the deployed `aws_region` (default
     `eu-central-1`).
   - **Workgroup**: `terraform output athena_workgroup_name`.
   - **S3 results bucket**: leave using the workgroup's own
     configured output location (already set on the workgroup itself
     in `athena.tf`) rather than re-entering it.
   - **Authentication**: leave credentials blank — the EC2 instance's
     own role (`MetabaseInstanceRole`, see `iam.tf`) already has
     exactly the Athena/Glue/S3 permissions Metabase needs; the
     Athena JDBC driver picks up the instance's credentials
     automatically via the EC2 instance metadata service.
4. Once connected, Metabase auto-discovers the three tables under
   `terraform output athena_database_name` (default
   `procurement_gold`) — `eea_measurements`, `ted_notices`,
   `eurostat_agriculture_accounts`.

The dashboard's own visualizations (filters, aggregations, chart
choices per source) are specified separately — see the dashboard spec
this stack was built against; the Gold columns each visualization
needs are exactly what `glue.tf`'s three tables expose, confirmed
against the real column names/types in `gold/eea/measurements.py`,
`gold/ted/notices.py`, and `gold/eurostat/agriculture_accounts.py`
(and each source's dtype-casting in `normalization/`).

## Access model

Metabase is the only always-on, publicly-reachable resource this
project runs — everything else is either a batch Fargate task (starts,
runs, exits) or a managed AWS service with no listening port of its
own. Its security group (`metabase.tf`) allows inbound TCP/3000 only
from `var.metabase_allowed_cidr_blocks`; there is no port 443/TLS
termination and no domain name in this setup — Metabase is served
over plain HTTP directly on port 3000, restricted to specific IPs
rather than protected by TLS. Revisit this (e.g. an ALB + ACM
certificate + a real domain) if Metabase ever needs to be reachable
from more than a small, known set of IPs.

Shell access to the instance is via **SSM Session Manager**
(`MetabaseInstanceRole` carries `AmazonSSMManagedInstanceCore`, see
`iam.tf`) — there is no SSH key pair and no inbound port 22:

```
aws ssm start-session --target <instance-id>
```

## Data durability

Metabase's own application database — dashboards, saved questions,
user accounts, *not* the Gold data itself — is its embedded H2 file,
bind-mounted from the container onto `/opt/metabase-data` on the
instance's own EBS root volume (`metabase.tf`'s
`root_block_device`). It survives instance reboots and stops, but is
tied to this one instance: replacing the instance (not just
restarting the container) loses it unless the volume itself is
snapshotted first. This project deliberately does not run a separate
RDS Postgres database for Metabase's app DB — that's a reasonable
next step if dashboard/user configuration durability across instance
replacement becomes worth the extra ~$15-25/month.

The Gold data Metabase queries is unaffected either way — it lives in
S3 (`data/gold/`), independent of the Metabase instance entirely.

## Cost

Rough monthly add-on beyond the existing pipeline spend, `eu-central-1`:

| Item | Approx. cost |
|---|---|
| EC2 `t3.micro` (default `metabase_instance_type`) | ~$8-9/month |
| EBS gp3, default 30GB (`metabase_volume_size_gb`) | ~$3/month |
| Elastic IP (attached to a running instance) | $0 — only unattached EIPs are charged |
| Glue Data Catalog (3 tables) | Free tier covers this easily |
| Athena queries | $5/TB scanned — negligible at this data volume |

`budget.tf`'s monthly limit was raised from $20 to $35 accordingly
(`variables.tf`'s `budget_limit_amount`) — still notification-only,
never auto-disables anything (see `operations.md`).
