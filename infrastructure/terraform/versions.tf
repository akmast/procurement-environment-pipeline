# Terraform >=1.10 is required for native S3 state locking
# (`use_lockfile`) — no DynamoDB lock table needed (see backend.hcl and
# docs/aws/deployment.md for why).
terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Bucket/key/region/use_lockfile are intentionally NOT set here — a
  # backend block cannot reference variables or Terraform-managed
  # resources, and the state bucket itself must exist before `terraform
  # init` can use it (see infrastructure/bootstrap/bootstrap-state-backend.sh).
  # Values are supplied at init time via `terraform init -backend-config=backend.hcl`.
  backend "s3" {}
}
