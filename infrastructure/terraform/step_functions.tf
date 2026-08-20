# Three Step Functions Standard state machines, each using the ECS
# RunTask.sync integration to invoke the same reusable task definition
# (see ecs.tf) with a different `main.py` command override per stage —
# no persistent ECS Service, no Lambda in the execution path. All three
# render the same set of template variables into their ASL definition
# (infrastructure/terraform/templates/*.asl.json.tpl).

locals {
  asl_template_vars = {
    ecs_cluster_arn         = aws_ecs_cluster.pipeline.arn
    ecs_task_definition_arn = aws_ecs_task_definition.pipeline.arn
    container_name          = local.container_name
    data_bucket_name        = var.data_bucket_name
    subnet_ids_json         = jsonencode(aws_subnet.public[*].id)
    security_group_ids_json = jsonencode([aws_security_group.pipeline_task.id])
  }
}

# BootstrapReferenceStateMachine — manual only. Prepares NUTS boundaries,
# TED codelists, and EEA stations, then writes the bootstrap completion
# manifest historical/update gate on. Never runs on a schedule and is
# never invoked automatically after a deploy.
resource "aws_sfn_state_machine" "bootstrap_reference" {
  name     = "BootstrapReferenceStateMachine"
  role_arn = aws_iam_role.step_functions.arn
  type     = "STANDARD"

  definition = templatefile(
    "${path.module}/templates/bootstrap_reference.asl.json.tpl",
    local.asl_template_vars
  )

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.step_functions.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  tags = local.common_tags
}

# HistoricalStateMachine — manual only (workflow_dispatch or a manual
# StartExecution), never auto-run after a merge to main and never
# attached to a schedule.
resource "aws_sfn_state_machine" "historical" {
  name     = "HistoricalStateMachine"
  role_arn = aws_iam_role.step_functions.arn
  type     = "STANDARD"

  definition = templatefile(
    "${path.module}/templates/historical.asl.json.tpl",
    local.asl_template_vars
  )

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.step_functions.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  tags = local.common_tags
}

# UpdateStateMachine — the monthly incremental/refresh run. Triggered by
# EventBridge Scheduler (see scheduler.tf, created DISABLED) or manually.
resource "aws_sfn_state_machine" "update" {
  name     = "UpdateStateMachine"
  role_arn = aws_iam_role.step_functions.arn
  type     = "STANDARD"

  definition = templatefile(
    "${path.module}/templates/update.asl.json.tpl",
    local.asl_template_vars
  )

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.step_functions.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  tags = local.common_tags
}
