# Operations

## Running a pipeline

Use **Actions → Run Pipeline → Run workflow** in GitHub, or call
`StartExecution` directly (see `deployment.md`'s "First safe run" for
exact commands). Never run a full historical backfill without
deciding that's what you want — there's no dry-run mode and it
downloads real data from real APIs.

| `state_machine` input | Sources param | Notes |
|---|---|---|
| `bootstrap-reference` | ignored | Run once initially, and again whenever reference data needs updating (rare — see `architecture.md`) |
| `historical` | `eea ted eurostat` (space-separated) | Requires `from_year`/`to_year`; manual only, never scheduled |
| `update` | `eea ted eurostat` | What the monthly schedule runs |
| `gold-standard` | ignored | Rebuilds the Gold Layer — always all three sources, always from everything currently normalized/transformed (see `docs/pipelines/gold_layer.md`); manual only, never scheduled, never auto-chained after `update`/`historical`. Run it as a deliberate step once the sources you care about are up to date. |

## Viewing a Step Functions execution

- **Console**: Step Functions → State machines → pick one → the
  execution list shows status/duration; click an execution for the
  visual graph, and click any state for its input/output/error.
- **CLI**:
  ```
  aws stepfunctions describe-execution --execution-arn <arn>
  aws stepfunctions get-execution-history --execution-arn <arn>
  ```
- `run-pipeline.yml`'s job summary prints the exact console URL and CLI
  command for the execution it just started.

## Viewing ECS/CloudWatch logs

Every container run logs to CloudWatch Logs group `/ecs/procurement-environment-pipeline`,
stream prefix `pipeline/<container>/<ecs-task-id>`.

- **Console**: CloudWatch → Log groups → `/ecs/procurement-environment-pipeline`
  → pick the stream for the task/timeframe you care about.
- **CLI**:
  ```
  aws logs tail /ecs/procurement-environment-pipeline --follow
  aws ecs describe-tasks --cluster procurement-environment-pipeline --tasks <task-id>
  ```
- Each log line includes run ID, source, stage, mode, country, period
  (where applicable), counts of written/unchanged/failed paths, and
  final status — see the logging conventions in each module and
  `main.py`'s `_execute_stage`.
- Step Functions' own execution history (separate from container logs)
  is in CloudWatch Logs group `/aws/vendedlogs/states/procurement-environment-pipeline`.

## Re-running after a failure

1. Check the failing state in the Step Functions execution graph —
   its `error`/`cause` (from the `Catch` block) names what failed, and
   which source/stage it was (e.g. `TedRunNormalization`).
2. Check that state's ECS task logs in CloudWatch for the actual
   Python traceback/error.
3. Decide whether the fix is code (push it, wait for `deploy.yml` to
   build a new image) or data/config (e.g. a source API changed shape
   — check `docs/pipelines/*.md` for known quirks).
4. Re-run. Two options:
   - **Full re-run** (default): **Actions → Run Pipeline** with the
     same inputs, leaving `run_id`/`start_stage` empty. Each run gets
     a fresh `run_id`; nothing about a prior failed run needs manual
     cleanup — ingestion's own change-tracking/state means a re-run
     naturally only reprocesses what didn't already succeed (see
     `docs/storage_and_incremental.md`).
   - **Resume from the failed stage**: if ingestion (or normalization)
     for a source already completed and only a later stage failed,
     re-running the whole chain re-executes stages that already
     succeeded for no reason — set `run_id` to the failed execution's
     own `run_id` (visible in its Step Functions input/output, or the
     failing task's `--run-id` argument in its ECS logs) and
     `start_stage` to the stage that failed (`normalization` or
     `transformation`). The skipped earlier stages' manifests are
     still sitting at `runs/<run_id>/<source>/<stage>.json` in S3 from
     the original attempt, so the resumed stage picks up exactly where
     it left off via `--input-manifest`, same as normal chaining. Note
     AWS Step Functions' own [Redrive](https://docs.aws.amazon.com/step-functions/latest/dg/redrive-executions.html)
     isn't useful here as-is — every source's stages share one `Catch`
     that routes into that branch's own `*Failed` Pass state, so the
     execution's actual failed state (from Redrive's point of view) is
     the top-level `HistoricalFailed`/`UpdateFailed` Fail state, not
     the specific task that failed; `run_id`/`start_stage` is the
     practical workaround for this MVP instead of restructuring the
     state machines to make Redrive state-accurate.

## Enabling / disabling the monthly schedule

The schedule (`procurement-environment-pipeline-monthly-update`,
first Monday of the month, 03:00 Europe/Berlin) is created **DISABLED**
and stays that way until you enable it — see `deployment.md`'s "First
safe run". To disable it again:

```
aws scheduler update-schedule --name procurement-environment-pipeline-monthly-update \
  --group-name default --state DISABLED \
  --schedule-expression 'cron(0 3 ? * MON#1 *)' \
  --schedule-expression-timezone Europe/Berlin \
  --flexible-time-window '{"Mode": "OFF"}' \
  --target '<paste the existing target block from `aws scheduler get-schedule`>'
```

Or set `update_schedule_enabled = false` in Terraform and redeploy —
simpler if you don't need it done outside a deploy.

**Verify the `cron(0 3 ? * MON#1 *)` expression against the current
AWS EventBridge Scheduler documentation before relying on it in
production** — it wasn't executed against real AWS in the environment
this was built in (no live AWS access there); a quick way to confirm
without waiting a month is `aws scheduler get-schedule --name ...` to
inspect the next few computed fire times, if the API exposes them, or
a short-lived test schedule with a near-term one-off cron.

## Approximate cost drivers

Ordered roughly by materiality for this MVP's traffic pattern (a few
Fargate task-hours per month, small S3 volumes):

1. **Fargate compute** — billed per vCPU/memory-second while a task
   runs. Default 1 vCPU/4GB; historical/bootstrap runs take longer
   than incremental updates. Tune `fargate_cpu`/`fargate_memory`
   (Terraform variables) if runs are slow or over-provisioned.
2. **S3 storage** — raw/normalized/transformed data + Versioning's
   extra copies of changed objects. Lifecycle rules already bound
   noncurrent-version growth (90 days) and clean up orphaned staging
   objects (1 day) — see `s3.tf`.
3. **CloudWatch Logs** — retention set to 90 days
   (`log_retention_days`); lower it if log volume/cost becomes
   noticeable.
4. **ECR storage** — bounded by the lifecycle policy (30 most recent
   `sha-`-tagged images, untagged images expire after 7 days).
5. **Data transfer** — outbound from Fargate to the source APIs and
   from S3; small at this data scale.

**No NAT Gateway cost** (see `architecture.md` for the trade-off this
accepts). No idle compute — nothing runs between invocations.

## Cost budget and notifications

`budget.tf` creates a monthly AWS Budgets **COST** budget
(`procurement-environment-pipeline-monthly-budget`, `budget_limit_amount`
= 20 by default, `budget_currency` = EUR by default) scoped to this
project's resources via the `Project` cost allocation tag, with email
notifications at 80% and 100% of the limit. **It is notification-only —
it never stops, disables, or deletes anything**, by design (see
`budget.tf`'s own comment for why a hard spend cap isn't implemented).

Two things must be true for the numbers it reports to actually reflect
this project's spend:

1. **The `Project` tag must be activated as a cost allocation tag** —
   a one-time, manual, non-destructive step (not something Terraform
   does automatically — see `budget.tf`'s comment for why):
   ```
   aws ce update-cost-allocation-tags-status \
     --cost-allocation-tags-status TagKey=Project,Status=Active
   ```
   Do this only after the project's resources have existed for a
   while — a brand-new tag can take up to 24 hours to appear as
   activatable in Cost Explorer/Billing. Check current status with:
   ```
   aws ce list-cost-allocation-tags --status Active --query "CostAllocationTags[?TagKey=='Project']"
   ```
2. **Fargate tasks must carry the tag on the running task, not just
   the task definition** — every `ecs:RunTask` call in the Step
   Functions templates already sets `PropagateTags: TASK_DEFINITION`
   and `EnableECSManagedTags: true`, so this happens automatically;
   nothing to do here, just know why compute costs show up under the
   tag.

Even once both are true, the reported number is an **approximation**,
not a mirror of your invoice — see `deployment.md`'s note on
`budget_currency` for why (AWS tracks the underlying cost data in USD
internally and converts to EUR using its own exchange rate).

## Safe infrastructure teardown

1. **Disable the schedule first** (see above) so nothing fires mid-teardown.
2. Decide whether to keep the data: S3 buckets with objects can't be
   destroyed by Terraform without emptying them first (`aws s3 rm
   s3://<bucket> --recursive`, or add `force_destroy = true` to the
   bucket resource *deliberately*, then apply — don't do this by
   default).
3. `terraform destroy` from `infrastructure/terraform` (after
   `terraform init -backend-config=backend.hcl`). This removes every
   resource this Terraform manages — it does **not** touch
   `GitHubDeployRole`, the OIDC provider, or the Terraform state
   backend bucket itself (all pre-existing/out-of-band).
4. If the state backend bucket should also go: empty it and delete it
   manually (`aws s3 rb s3://procurement-pipeline-tfstate-137307166874
   --force`) — it's intentionally outside Terraform's own management
   (see `deployment.md`).
