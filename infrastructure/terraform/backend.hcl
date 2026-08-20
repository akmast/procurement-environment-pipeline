# Partial backend configuration for `terraform init -backend-config=backend.hcl`.
# None of these values are confidential (bucket/key names, region) — safe
# to commit. The bucket itself is created idempotently by
# infrastructure/bootstrap/bootstrap-state-backend.sh before this is ever
# used (see docs/aws/deployment.md).
bucket       = "procurement-pipeline-tfstate-137307166874"
key          = "procurement-environment-pipeline/terraform.tfstate"
region       = "eu-central-1"
encrypt      = true
use_lockfile = true
