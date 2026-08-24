# Pipeline data bucket — raw/normalized/transformed data, per-run
# manifests under runs/<run_id>/<source>/<stage>.json, and the bootstrap
# completion marker under system/bootstrap/reference/latest.json (see
# common/manifest.py and common/bootstrap.py). Versioning is enabled here,
# once, by Terraform — application code never toggles bucket versioning
# on every write.
resource "aws_s3_bucket" "pipeline_data" {
  bucket = var.data_bucket_name

  tags = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "pipeline_data" {
  bucket = aws_s3_bucket.pipeline_data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "pipeline_data" {
  bucket = aws_s3_bucket.pipeline_data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "pipeline_data" {
  bucket = aws_s3_bucket.pipeline_data.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "pipeline_data" {
  bucket = aws_s3_bucket.pipeline_data.id

  # common/staged_write.py always deletes its own staging/<final_path>
  # object in a `finally` block immediately after each write attempt —
  # this rule only cleans up objects orphaned by a task that crashed or
  # was killed mid-write, so staging/ never grows unbounded.
  rule {
    id     = "expire-orphaned-staging-objects"
    status = "Enabled"

    filter {
      prefix = "staging/"
    }

    expiration {
      days = 1
    }
  }

  # Bounds the storage cost growth Versioning otherwise causes, while
  # still keeping a reasonable rollback window.
  rule {
    id     = "expire-old-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # Athena query results (athena.tf's workgroup writes here) are
  # throwaway once read — Metabase/Athena always re-query the Gold
  # tables directly, nothing depends on a past result staying around.
  rule {
    id     = "expire-old-athena-results"
    status = "Enabled"

    filter {
      prefix = "athena-results/"
    }

    expiration {
      days = 7
    }
  }
}

# Reject any non-TLS request to the data bucket — the only bucket policy
# this project needs; all real access control is via PipelineTaskRole's
# IAM policy (see iam.tf), not bucket policy.
resource "aws_s3_bucket_policy" "pipeline_data_require_tls" {
  bucket = aws_s3_bucket.pipeline_data.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.pipeline_data.arn,
          "${aws_s3_bucket.pipeline_data.arn}/*",
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })
}
