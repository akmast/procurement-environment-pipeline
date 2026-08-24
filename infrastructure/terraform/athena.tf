# Athena workgroup for querying the Gold Layer through the Glue Catalog
# (glue.tf). Query results are written to the existing pipeline data
# bucket under athena-results/ — no new bucket needed, and s3.tf already
# has a lifecycle rule expiring an analogous throwaway prefix
# (staging/) so the same pattern is reused for these.
resource "aws_athena_workgroup" "gold" {
  name = "${local.name_prefix}-gold"

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = false

    result_configuration {
      output_location = "s3://${var.data_bucket_name}/athena-results/"

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }

  tags = local.common_tags
}
