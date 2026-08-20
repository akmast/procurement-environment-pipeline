# Troubleshooting

## GitHub OIDC / AWS auth failures

`configure-aws-credentials` fails to assume `GitHubDeployRole`. Check,
in order:

1. **Workflow permissions** — the failing workflow must declare:
   ```yaml
   permissions:
     contents: read
     id-token: write
   ```
   Both `deploy.yml` and `run-pipeline.yml` already do; if you copied
   either into a new workflow, don't drop this block.
2. **Repository variables** — `AWS_REGION` and `AWS_ROLE_ARN` must be
   set under repo Settings → Secrets and variables → Actions →
   Variables (not Secrets — these two values aren't confidential).
   ```
   gh variable list
   ```
3. **Branch** — the OIDC trust policy's `sub` claim is pinned to
   `repo:akmast@28808405/procurement-environment-pipeline@1339244997:ref:refs/heads/main`
   — a workflow run from any other branch or a fork will be denied.
   This is deliberate; don't widen it without deciding that's safe.
4. **Audience** — must be `sts.amazonaws.com` (the default
   `aws-actions/configure-aws-credentials` uses; don't override it
   unless the trust policy also changes).
5. **Diagnostic command** (once credentials are configured):
   ```
   aws sts get-caller-identity
   ```
   A failure here with "not authorized to perform sts:AssumeRoleWithWebIdentity"
   means the trust policy's conditions (audience/subject) don't match
   what this specific workflow run presents — re-check items 3-4.

**Never fall back to AWS Access Keys** to work around an OIDC failure
— fix the trust policy/workflow permissions/variables instead. If the
trust policy itself needs changing, that's a manual AWS Console/CLI
action outside this Terraform (see `deployment.md`'s prerequisites) —
don't have Terraform manage it silently as a side effect of an
unrelated change.

## Terraform state lock

Native S3 locking (`use_lockfile = true`) can leave a stale lock if a
previous `apply`/`plan` was killed mid-run (e.g. a cancelled Actions
job). Symptom: `terraform plan`/`apply` hangs or errors with a lock
message naming a lock ID and "who" holds it.

1. Confirm no other deploy is actually still running (check the
   Actions tab) before doing anything.
2. `terraform force-unlock <lock-id>` from a session with the same AWS
   credentials/backend config.

## Terraform apply fails on a resource that already exists

Usually means a resource was created outside Terraform (manually, or
by a partially-failed earlier apply using `-target`). Import it rather
than deleting and recreating:

```
terraform import <resource_address> <aws-resource-id-or-arn>
```

## ECS task fails immediately (before any application log appears)

Check `aws ecs describe-tasks --cluster procurement-environment-pipeline
--tasks <task-id>` for `stoppedReason` — common causes:

- **Image pull failure**: the `container_image_tag` Terraform was
  applied with doesn't exist in ECR yet (see `deployment.md`'s
  first-deployment ordering — build/push must happen before the final
  `terraform apply`).
- **Task role/execution role permission gap**: `stoppedReason`
  mentions IAM — check `iam.tf`'s `EcsTaskExecutionRole`/`PipelineTaskRole`
  policies against what actually failed.
- **No public IP / can't reach ECR or the source APIs**: confirm the
  task's subnet is one of `aws_subnet.public` and
  `AssignPublicIp: "ENABLED"` is set in the Step Functions
  `NetworkConfiguration` (see `step_functions.tf`'s `asl_template_vars`
  and the `.asl.json.tpl` files).

## Step Functions execution fails at `CheckBootstrapComplete`

Expected behavior if bootstrap hasn't run yet, or ran incompletely —
see the `BootstrapIncomplete` Fail state's cause, and
`common/bootstrap.py`'s own error message. Fix: run
`BootstrapReferenceStateMachine` (see `deployment.md`), then re-run
historical/update.

## A source's branch fails inside `HistoricalStateMachine`/`UpdateStateMachine`

Each source family (`eea`/`ted`/`eurostat`) runs as an independent
`Parallel` branch with its own internal `Catch` — one source failing
doesn't stop the others, but the overall execution still ends in
`Fail` status (`EvaluateOverallStatus` checks every branch's result).
Check that specific source's ECS task logs (see `operations.md`) for
the actual error; re-running the whole state machine is safe (each
stage's own change-tracking means already-succeeded work doesn't
redo unnecessary API calls where the underlying source module supports
that — see `docs/pipelines/*.md`).

## `terraform validate` / provider download issues

If `terraform init` can't reach `registry.terraform.io` (e.g. a
restrictive network policy — this happened in the environment this
Terraform was originally authored in, which is why full
`terraform validate` was never executed there, only `terraform fmt`
and structural JSON validation of the Step Functions definitions): run
`terraform validate` from an environment with normal internet access
(a real GitHub Actions runner has this) before trusting a `plan`/
`apply` from a heavily network-restricted sandbox.

## Docker build fails locally

If Docker isn't available at all in your environment (no daemon), you
can't build/test the image locally — this is a hard requirement for
`deploy.yml` (GitHub-hosted runners have Docker preinstalled) but not
for iterating on the pipeline's own Python code, which runs fine
without Docker via `uv run python3 main.py ...`.
