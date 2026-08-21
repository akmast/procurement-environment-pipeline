# procurement-environment-pipeline
Data engineering pipeline integrating EU public procurement, air quality, and agricultural data to analyze environmental spending and sustainability trends across European regions.

## Data sources

- **TED** — public procurement data
- **EEA** — air quality data
- **Eurostat** — regional and agricultural data

## Pipeline

Each source has its own `ingestion` module (raw data download),
`normalization` module (converting raw data into a common format), and
(for EEA and TED) a `transformation` module (dedup, joins, derived
fields). Shared logic lives in `common`. `main.py` is the single CLI
entry point for running any stage locally or in AWS — see
`main.py --help`.

## Running locally

```
uv sync
uv run python3 main.py stage --source eea-measurements --stage ingestion --mode test
uv run python3 main.py pipeline update --sources eea ted eurostat --storage-mode local
```

`storage_mode="local"` (the default) reads/writes under `data/` in the
repo; `storage_mode="cloud"` uses S3 via the `PIPELINE_S3_BUCKET`
environment variable — see `docs/storage_and_incremental.md`.

## AWS deployment

This pipeline also runs in AWS: GitHub Actions (OIDC, no long-lived
keys) + Terraform + ECR + ECS Fargate (batch tasks, no persistent
service) + Step Functions + EventBridge Scheduler + S3. See
`docs/aws/architecture.md` for the full design,
`docs/aws/deployment.md` for first-deployment steps, and
`docs/aws/operations.md` / `docs/aws/troubleshooting.md` for running
and debugging it day to day.
