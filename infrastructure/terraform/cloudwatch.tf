resource "aws_cloudwatch_log_group" "ecs" {
  name              = local.ecs_log_group_name
  retention_in_days = var.log_retention_days

  tags = local.common_tags
}

# Step Functions "vended logs" — the /aws/vendedlogs/states/ prefix is
# the AWS-documented naming convention that keeps the CloudWatch Logs
# resource policy for cross-service log delivery scoped and simple; the
# actual permission grant is still explicit on StepFunctionsRole (see
# iam.tf), this naming is a convention, not a permission by itself.
resource "aws_cloudwatch_log_group" "step_functions" {
  name              = local.step_functions_log_group_name
  retention_in_days = var.log_retention_days

  tags = local.common_tags
}
