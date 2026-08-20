# Monthly update trigger — first Monday of the month, 03:00
# Europe/Berlin. Created DISABLED: the user enables it herself only
# after a successful manual UpdateStateMachine test run (see
# docs/aws/operations.md). Never triggers HistoricalStateMachine or
# BootstrapReferenceStateMachine.
resource "aws_scheduler_schedule" "monthly_update" {
  name       = "${local.name_prefix}-monthly-update"
  group_name = "default"

  schedule_expression          = var.update_schedule_expression
  schedule_expression_timezone = var.update_schedule_timezone

  state = var.update_schedule_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.update.arn
    role_arn = aws_iam_role.scheduler.arn

    input = jsonencode({
      sources       = ["eea", "ted", "eurostat"]
      countries_csv = "DE,PL"
    })

    retry_policy {
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 3600
    }
  }
}
