# Procurement Environment Pipeline

A cloud-ready data engineering pipeline integrating European public procurement, air quality, and agricultural data into standardized regional datasets.

The project creates a common analytical foundation for comparing environmental, agricultural, and procurement indicators across European NUTS regions.

## Documentation

Detailed project documentation is stored in the [`docs/`](docs/) directory:

```text
docs/
├── aws/
│   ├── analytics.md
│   ├── architecture.md
│   ├── deployment.md
│   ├── operations.md
│   └── troubleshooting.md
├── pipelines/
│   ├── countries.md
│   ├── eea_measurements.md
│   ├── eea_nuts_boundaries.md
│   ├── eea_stations.md
│   ├── eurostat_agriculture_accounts.md
│   ├── gold_layer.md
│   ├── ted_codelists.md
│   └── ted_notices.md
└── storage_and_incremental.md
```

### AWS documentation

The [`docs/aws/`](docs/aws/) directory describes the cloud infrastructure and its operation:

| Document | Contents |
|---|---|
| [`architecture.md`](docs/aws/architecture.md) | AWS services, Step Functions workflows, Fargate execution, manifests, dependencies, networking and IAM roles |
| [`deployment.md`](docs/aws/deployment.md) | Initial AWS setup, GitHub OIDC, Terraform backend, GitHub Actions variables and deployment steps |
| [`operations.md`](docs/aws/operations.md) | Running Bootstrap, Historical, Update and Gold workflows; viewing executions and CloudWatch logs |
| [`analytics.md`](docs/aws/analytics.md) | Gold tables, Glue Data Catalog, Athena workgroup and BI-tool access |
| [`troubleshooting.md`](docs/aws/troubleshooting.md) | OIDC, Terraform, ECS, Step Functions and deployment troubleshooting |

### Pipeline documentation

The [`docs/pipelines/`](docs/pipelines/) directory documents each data source from API request to final output:

| Document | Contents |
|---|---|
| [`countries.md`](docs/pipelines/countries.md) | Multi-country processing, ISO2 country codes, explicit partitions and storage layout |
| [`ted_notices.md`](docs/pipelines/ted_notices.md) | TED queries, environmental CPV filters, JSONL storage, pagination, date cursor and deduplication |
| [`ted_codelists.md`](docs/pipelines/ted_codelists.md) | TED reference codelists, Genericode XML parsing and label enrichment |
| [`eea_measurements.md`](docs/pipelines/eea_measurements.md) | Air-quality API requests, pollutant-specific Parquet files and refresh logic |
| [`eea_stations.md`](docs/pipelines/eea_stations.md) | Station metadata, coordinates, ArcGIS pagination and joins with measurements |
| [`eea_nuts_boundaries.md`](docs/pipelines/eea_nuts_boundaries.md) | GISCO NUTS3 boundaries and spatial mapping of stations to NUTS regions |
| [`eurostat_agriculture_accounts.md`](docs/pipelines/eurostat_agriculture_accounts.md) | Eurostat requests, JSON-stat 2.0 cube structure, parsing and historical updates |
| [`gold_layer.md`](docs/pipelines/gold_layer.md) | Gold schemas, selected columns, table-building logic and final Parquet outputs |

### Storage and incremental processing

[`docs/storage_and_incremental.md`](docs/storage_and_incremental.md) describes shared pipeline mechanics:

- Local filesystem and S3 storage modes
- Staging before final writes
- File validation
- SHA-256 content comparison
- Per-country state files
- S3 versioning
- EEA reporting-window rules
- Incremental and idempotent processing

## Data Sources

| Source | Data |
|---|---|
| **TED — Tenders Electronic Daily** | Environment-related public procurement notices |
| **EEA — European Environment Agency** | Air-quality measurements: PM10, PM2.5, NO₂, O₃ and SO₂ |
| **Eurostat** | Regional Economic Accounts for Agriculture (`aact_eaa01_r`) |

The sources have different APIs, formats, geographical structures, and update rules:

- TED returns nested JSON notices.
- EEA provides links to Parquet files.
- Eurostat returns multidimensional JSON-stat 2.0 data cubes.

## Data Pipeline

```text
Raw
 ↓
Normalized
 ↓
Transformed
 ↓
Gold
```

- **Raw:** original API responses and downloaded files
- **Normalized:** schemas, data types and standardized column names
- **Transformed:** reference-data joins and NUTS enrichment
- **Gold:** one analytics-ready Parquet table per source

Eurostat has no separate transformation stage. Its Gold table is built directly from normalized data.

## Incremental Processing

Each source follows its own update strategy:

- **TED:** date cursor stored per country and deduplication by `publication-number`
- **EEA:** reporting window and SHA-256 comparison of Parquet content
- **Eurostat:** repeated historical requests and content-hash comparison

Pipeline stages exchange small JSON manifests containing paths, status, counts, and execution metadata. Dataset content remains in S3.

## AWS Architecture

```text
GitHub Actions
      ↓
Amazon ECR
      ↓
AWS Step Functions
      ↓
ECS Fargate Tasks
      ↓
Amazon S3
      ↓
Glue Data Catalog
      ↓
Amazon Athena
```

- **GitHub Actions + OIDC:** secure deployment without permanent AWS keys
- **Terraform:** reproducible infrastructure
- **ECR:** versioned Docker images
- **ECS Fargate:** temporary batch containers
- **Step Functions:** workflow orchestration, retries and parallelism
- **EventBridge Scheduler:** scheduled updates
- **S3:** data layers, manifests and Athena results
- **CloudWatch:** centralized logs
- **Glue + Athena:** serverless SQL access to Gold data

The pipeline runs in public VPC subnets with outbound-only Security Group rules and no NAT Gateway. There is no Lambda or persistent ECS service in the execution path.

## State Machines

| State Machine | Purpose | Trigger |
|---|---|---|
| `BootstrapReferenceStateMachine` | Prepare NUTS boundaries, TED codelists and EEA stations | Manual |
| `HistoricalStateMachine` | Load a selected historical period | Manual |
| `UpdateStateMachine` | Run source-specific incremental updates | Manual or EventBridge |

All workflows use the same ECS cluster, reusable task definition, and Docker image. Each state launches a temporary Fargate task with a different `main.py` command.

## Analytics

Gold Parquet files are exposed as three Glue tables:

- `eea_measurements`
- `ted_notices`
- `eurostat_agriculture_accounts`

Athena queries these tables directly in S3.

Metabase runs locally through Docker Compose and connects to Athena using a local AWS CLI or SSO profile:

```text
Local Metabase
      ↓
Amazon Athena
      ↓
Glue Data Catalog
      ↓
Gold Parquet in S3
```

Metabase is not deployed in AWS. Dashboards and saved questions are stored locally in a Docker volume.

## Running Locally

Install dependencies:

```bash
uv sync
```

Run a test stage:

```bash
uv run python3 main.py stage \
  --source eea-measurements \
  --stage ingestion \
  --mode test
```

Run an update locally:

```bash
uv run python3 main.py pipeline update \
  --sources eea ted eurostat \
  --storage-mode local
```

Local mode writes under `data/`. Cloud mode uses the S3 bucket configured through `PIPELINE_S3_BUCKET`.

## Local Metabase

```bash
cd local/metabase
cp .env.example .env
```

Set your AWS profile in `.env`:

```env
AWS_PROFILE=your-profile-name
AWS_REGION=eu-central-1
```

Start Metabase:

```bash
docker compose up -d
```

Open:

```text
http://localhost:3000
```

See [`local/metabase/README.md`](local/metabase/README.md) for Athena connection and IAM setup.
