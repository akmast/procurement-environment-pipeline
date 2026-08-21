# AWS Budgets — cost monitoring only. This resource never disables,
# stops, or deletes anything: it only tracks spend and sends email
# notifications at 80% and 100% of the monthly limit. Enforcing a hard
# spend cap (e.g. auto-stopping resources) is a deliberately separate,
# much riskier kind of automation this project does not implement.
#
# Scope: filtered to exactly this project's resources via the "Project"
# cost allocation tag — the same tag every resource in this Terraform
# already receives through providers.tf's `default_tags` block. AWS
# only includes a user-defined tag in cost/budget calculations once
# it's been activated as a Cost Allocation Tag for the account (Billing
# console → Cost allocation tags, or `aws ce update-cost-allocation-tags-status`
# — see docs/aws/operations.md). That's a one-time manual step, not
# something this Terraform does automatically: activation depends on
# AWS having already indexed the tag from real tagged resources (up to
# 24 hours after they're created), so doing it in the same `apply` that
# creates those resources would fail on a fresh account.
resource "aws_budgets_budget" "project_monthly_cost" {
  name         = "${local.name_prefix}-monthly-budget"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_amount)
  limit_unit   = var.budget_currency
  time_unit    = "MONTHLY"

  cost_filter {
    name = "TagKeyValue"
    # AWS's cost-allocation-tag filter format is "user:<TagKey>$<TagValue>"
    # for a user-defined tag (as opposed to "aws:..." for AWS-generated
    # tags). Built with format() rather than string interpolation to
    # avoid the literal "$" colliding with Terraform's own "${...}"
    # interpolation syntax.
    values = [format("user:Project$%s", var.project_name)]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_notification_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_notification_email]
  }

  tags = local.common_tags
}
