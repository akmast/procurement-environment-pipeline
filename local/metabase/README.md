# Local Metabase

Runs Metabase locally in Docker, reading the Gold Layer through the
project's existing AWS Athena workgroup and Glue Data Catalog
(`infrastructure/terraform/athena.tf`, `glue.tf`) — nothing new is
created in AWS for this. It replaces the earlier EC2-hosted Metabase
instance (removed; see `docs/aws/analytics.md` for why) with a plain
local container you start/stop yourself.

Gold data never leaves S3 — Metabase only sends SQL to Athena and
receives results back. The only thing stored locally (in a Docker
volume, not in this repo) is Metabase's own app state: your admin
user, saved questions, dashboards, and visualization settings.

## Prerequisites

- Docker and Docker Compose.
- A local AWS CLI or AWS SSO profile with access to account
  `137307166874` (`aws configure list-profiles` to see what you have).
  That profile needs the permissions in `iam-policy.json` — see
  **AWS authentication and permissions** below.

## First run

```
cd local/metabase
cp .env.example .env
```

Edit `.env` and set `AWS_PROFILE` to your profile's name (`AWS_REGION`
already defaults to `eu-central-1`, matching the deployed stack).

```
docker compose up -d
```

Open **http://localhost:3000** — Metabase's own setup wizard runs on
first load; create an admin account (this account is local to the
container, unrelated to AWS IAM).

## AWS authentication and permissions

No AWS keys are ever written into this repo, the image, or `.env`.
`docker-compose.yml` mounts your local `~/.aws` directory into the
container **read-only** and sets `AWS_PROFILE`; Metabase's Athena
driver (built on the AWS SDK) then resolves credentials through the
standard AWS credentials provider chain using that profile — the same
one your `aws` CLI already uses on this machine. If your profile uses
AWS SSO, its cached token has a limited lifetime: run
`aws sso login --profile <your-profile>` on the host whenever queries
start failing with an authentication error.

Your profile needs permission to query the Gold Layer via Athena/Glue.
**`iam-policy.json` in this directory is the minimal policy for
that** — read/run Athena queries against the existing
`procurement-environment-pipeline-gold` workgroup, read the existing
`procurement_gold` Glue database and its three tables, read the Gold
Parquet files from S3, and read/write the existing
`athena-results/` prefix. Nothing more — no write/delete access to
Gold data, no access to any other AWS resource in this project. It's
the same permission logic the former EC2 Metabase instance's IAM role
used (see `infrastructure/terraform/iam.tf`'s git history), reused
here without re-creating that role or its EC2 instance.

This file is **not** applied by Terraform and grants nothing by
itself — attach it yourself to whichever IAM user/role your
`AWS_PROFILE` resolves to, e.g.:

```
aws iam put-user-policy \
  --user-name <your-iam-user-name> \
  --policy-name local-metabase-gold-readonly \
  --policy-document file://iam-policy.json
```

(or `put-role-policy --role-name <role>` if your profile assumes a
role). Review the policy first — attaching it changes real AWS
permissions on your account.

## Connecting Metabase to Athena

Metabase's Athena connection setup can't be safely automated here
without either storing credentials somewhere or driving Metabase's
API in a way that could silently break on a version bump — so this is
a one-time manual step, done once through the UI:

1. In Metabase, go to **Admin settings → Databases → Add a database**.
2. Pick **Amazon Athena** (Metabase's built-in driver since 0.40 — no
   plugin to install).
3. Fill in:
   - **Display name**: whatever you like, e.g. `Gold Layer`.
   - **Region**: `eu-central-1`
   - **Workgroup**: `procurement-environment-pipeline-gold`
   - **S3 Staging Directory**:
     `s3://procurement-pipeline-137307166874-eu-central-1/athena-results/`
   - **Access key** / **Secret key**: leave **both blank** — this is
     what makes Metabase fall back to the AWS credentials provider
     chain (your mounted `AWS_PROFILE`) instead of expecting static
     keys typed into the UI.
4. Save. Metabase syncs the connection's schemas from the Glue Catalog
   — this can take a few seconds to a couple of minutes.
5. **Verify**: open the new database from the main nav ("Browse
   data") — you should see schema `procurement_gold` containing three
   tables: `eea_measurements`, `ted_notices`,
   `eurostat_agriculture_accounts`. If they don't show up yet, use
   **Admin settings → Databases → (this connection) → Sync database
   schema now**.
6. **Test query** (only run this once you're ready — it executes a
   real, billed Athena query, even though the cost at this data volume
   is negligible): **New → SQL query**, pick the Gold Layer database,
   and run e.g.:
   ```sql
   SELECT COUNT(*) FROM eea_measurements;
   ```

## Commands

Run these from `local/metabase/`:

| Action | Command |
|---|---|
| First run / start | `docker compose up -d` |
| View logs | `docker compose logs -f` |
| Stop (keeps data) | `docker compose stop` |
| Start again | `docker compose start` |
| Recreate the container, keep dashboards | `docker compose up -d --force-recreate` |
| **Full removal — deletes all dashboards/questions/users** | `docker compose down -v` |

`docker compose down` (**without** `-v`) stops and removes the
container but leaves the named `metabase-data` volume intact — your
dashboards survive. Only `-v` (or a manual `docker volume rm`) actually
deletes Metabase's app state. The Gold data itself is never affected
either way — it isn't stored locally at all.
