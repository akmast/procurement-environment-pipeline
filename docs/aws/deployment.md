# Deployment

## Prerequisites (already configured, not part of this Terraform)

- AWS account `137307166874`, region `eu-central-1`.
- GitHub OIDC provider (`https://token.actions.githubusercontent.com`,
  audience `sts.amazonaws.com`) trusting this repository.
- IAM role `GitHubDeployRole`, assumable by GitHub Actions via OIDC —
  Terraform attaches a scoped deployment policy to it (`iam.tf`) but
  does not create the role or touch its trust policy.
- GitHub repository variables: `AWS_REGION=eu-central-1`,
  `AWS_ROLE_ARN=arn:aws:iam::137307166874:role/GitHubDeployRole`.

No AWS Access Keys exist anywhere in this setup — `deploy.yml` and
`run-pipeline.yml` both authenticate via
`aws-actions/configure-aws-credentials` using OIDC.

## GitHub OIDC flow

1. GitHub Actions requests a short-lived OIDC token scoped to this
   workflow run (subject includes `repo:akmast@28808405/procurement-environment-pipeline@1339244997:ref:refs/heads/main`).
2. `aws-actions/configure-aws-credentials` exchanges that token for
   temporary AWS credentials by calling `sts:AssumeRoleWithWebIdentity`
   against `GitHubDeployRole`.
3. Every subsequent AWS CLI/Terraform call in that job uses those
   temporary credentials — nothing is stored between runs.

If this fails, see `troubleshooting.md`.

## First deployment

1. **Bootstrap the Terraform state backend** (idempotent — safe to
   re-run, and `deploy.yml` does this automatically on every run):
   ```
   bash infrastructure/bootstrap/bootstrap-state-backend.sh
   ```
   Creates `s3://procurement-pipeline-tfstate-137307166874` with
   versioning, encryption, and Block Public Access — nothing else.

2. **Push to `main`** (or merge a PR into it). `deploy.yml` then:
   - Assumes `GitHubDeployRole` via OIDC, verifies identity.
   - Runs the state-backend bootstrap script (no-op if already done).
   - `terraform init -backend-config=backend.hcl`.
   - `terraform fmt -check`, `terraform validate`, `terraform plan`
     (plan here is a preview/lint step — see step below for what's
     actually applied).
   - `terraform apply -target=aws_ecr_repository.pipeline` — creates
     *only* the ECR repository first, so there's somewhere to push to.
     A no-op on every later deploy once the repo exists.
   - Builds the Docker image, tags it `sha-<12-char commit SHA>`,
     pushes it to ECR.
   - `terraform apply -var="container_image_tag=sha-<...>"` — creates/
     updates everything else (S3, IAM, network, ECS cluster + task
     definition referencing the just-pushed image, all three state
     machines, the EventBridge Scheduler — created `DISABLED`).
   - Prints a deployment summary (Terraform outputs) to the job summary.

   This ordering only matters this precisely on the *very first*
   deploy — every later deploy is idempotent regardless.

3. **Deploy never runs any pipeline.** No `StartExecution` call exists
   in `deploy.yml`. To actually run something, see "First safe run"
   below or `operations.md`.

## First safe run

Do these **manually**, one at a time, and check the result before
moving to the next:

1. **Bootstrap reference data** — via GitHub Actions
   (`Actions → Run Pipeline → state_machine: bootstrap-reference`) or
   directly:
   ```
   aws stepfunctions start-execution \
     --state-machine-arn <bootstrap_reference_state_machine_arn output> \
     --input '{"countries_csv": "DE,PL"}'
   ```
   Confirm it succeeds and `system/bootstrap/reference/latest.json`
   reads `"status": "COMPLETE"` (`aws s3 cp s3://<bucket>/system/bootstrap/reference/latest.json -`).

2. **A small manual historical run**, not the full backfill — e.g. one
   country, a short year range:
   ```
   aws stepfunctions start-execution \
     --state-machine-arn <historical_state_machine_arn output> \
     --input '{"sources": ["eea"], "countries_csv": "DE", "from_year": 2024, "to_year": 2024}'
   ```
   **Do not run a full historical backfill without deciding that's
   what you want** — it downloads real data from real APIs and can
   take a long time; there's no dry-run mode.

3. **A manual update run** (exercises the exact path the monthly
   schedule will use):
   ```
   aws stepfunctions start-execution \
     --state-machine-arn <update_state_machine_arn output> \
     --input '{"sources": ["eea", "ted", "eurostat"], "countries_csv": "DE,PL"}'
   ```

4. **Only after a successful manual update run**, enable the monthly
   schedule yourself:
   ```
   aws scheduler update-schedule --name procurement-environment-pipeline-monthly-update \
     --group-name default --state ENABLED \
     --schedule-expression 'cron(0 3 ? * MON#1 *)' \
     --schedule-expression-timezone Europe/Berlin \
     --flexible-time-window '{"Mode": "OFF"}' \
     --target '<paste the existing target block from `aws scheduler get-schedule`>'
   ```
   (Simplest in practice: flip `update_schedule_enabled = true` in
   Terraform and redeploy — `aws scheduler update-schedule` above is
   the direct-CLI alternative if you don't want to touch Terraform.)

## Terraform state

- Backend: S3, bucket `procurement-pipeline-tfstate-137307166874`,
  key `procurement-environment-pipeline/terraform.tfstate`, region
  `eu-central-1`, native S3 locking (`use_lockfile = true`,
  requires Terraform ≥1.10 — see `infrastructure/terraform/versions.tf`).
- Backend config lives in `infrastructure/terraform/backend.hcl`
  (no secrets — bucket/key/region are not confidential) and is passed
  via `terraform init -backend-config=backend.hcl`, since a `backend`
  block cannot reference variables directly.
- State is **never** committed to Git and never kept only on an
  ephemeral GitHub Actions runner — every apply reads/writes the S3
  backend.

## Re-running after a failed deploy

`terraform apply` is safe to re-run — Terraform reconciles whatever
partial state exists against the desired state. If a deploy fails
mid-way:

1. Check the failed step's error in the Actions log.
2. Fix the underlying issue (bad variable, IAM permission gap, etc.).
3. Re-push (or manually re-run the workflow) — no manual cleanup is
   normally needed.

If Terraform reports a lock held by a stale/crashed run, see
`troubleshooting.md`.
