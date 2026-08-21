#!/usr/bin/env bash
# Idempotently creates the S3 bucket Terraform's own state lives in.
# Must run once, before the first `terraform init`, and is safe to
# re-run any time after (every step checks current state before acting).
#
# Only the state backend bucket is created here — never confuses this
# with the pipeline *data* bucket (s3.tf), which Terraform itself
# manages once the backend exists.
#
# Requires: AWS CLI v2, credentials already configured (this script
# assumes it runs under GitHubDeployRole via OIDC in CI, or an
# equivalent local AWS session for a manual first run).
set -euo pipefail

BUCKET="procurement-pipeline-tfstate-137307166874"
REGION="eu-central-1"

echo "Checking for Terraform state bucket: s3://${BUCKET}"

if aws s3api head-bucket --bucket "${BUCKET}" 2>/dev/null; then
  echo "Bucket already exists — nothing to create."
else
  echo "Bucket does not exist — creating it."
  if [ "${REGION}" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "${BUCKET}" --region "${REGION}"
  else
    aws s3api create-bucket \
      --bucket "${BUCKET}" \
      --region "${REGION}" \
      --create-bucket-configuration "LocationConstraint=${REGION}"
  fi
fi

echo "Ensuring versioning is enabled (required for state history/recovery)."
aws s3api put-bucket-versioning \
  --bucket "${BUCKET}" \
  --versioning-configuration Status=Enabled

echo "Ensuring default encryption is enabled."
aws s3api put-bucket-encryption \
  --bucket "${BUCKET}" \
  --server-side-encryption-configuration '{
    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}, "BucketKeyEnabled": true}]
  }'

echo "Ensuring Block Public Access is fully enabled."
aws s3api put-public-access-block \
  --bucket "${BUCKET}" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

echo "State backend bucket ready: s3://${BUCKET}"
echo "Next: terraform init -backend-config=infrastructure/terraform/backend.hcl"
