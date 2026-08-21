output "data_bucket_name" {
  description = "S3 bucket holding raw/normalized/transformed data and run manifests."
  value       = aws_s3_bucket.pipeline_data.bucket
}

output "ecr_repository_url" {
  description = "ECR repository URL to build/push/pull the pipeline image against."
  value       = aws_ecr_repository.pipeline.repository_url
}

output "ecs_cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.pipeline.name
}

output "ecs_task_definition_arn" {
  description = "ECS task definition ARN (unversioned family ARN also works for RunTask)."
  value       = aws_ecs_task_definition.pipeline.arn
}

output "bootstrap_reference_state_machine_arn" {
  description = "BootstrapReferenceStateMachine ARN — start manually before the first historical/update run."
  value       = aws_sfn_state_machine.bootstrap_reference.arn
}

output "historical_state_machine_arn" {
  description = "HistoricalStateMachine ARN — manual only."
  value       = aws_sfn_state_machine.historical.arn
}

output "update_state_machine_arn" {
  description = "UpdateStateMachine ARN — manual or via the monthly schedule."
  value       = aws_sfn_state_machine.update.arn
}

output "monthly_update_schedule_name" {
  description = "EventBridge Scheduler schedule name — check its state (DISABLED/ENABLED) before assuming it's live."
  value       = aws_scheduler_schedule.monthly_update.name
}

output "monthly_update_schedule_state" {
  description = "Current state of the monthly update schedule (should read DISABLED until manually enabled)."
  value       = aws_scheduler_schedule.monthly_update.state
}

output "ecs_log_group_name" {
  description = "CloudWatch Logs group for pipeline container output."
  value       = aws_cloudwatch_log_group.ecs.name
}

output "step_functions_log_group_name" {
  description = "CloudWatch Logs group for Step Functions execution history."
  value       = aws_cloudwatch_log_group.step_functions.name
}

output "vpc_id" {
  description = "VPC ID Fargate tasks run in."
  value       = aws_vpc.pipeline.id
}

output "public_subnet_ids" {
  description = "Public subnet IDs used by ECS RunTask NetworkConfiguration."
  value       = aws_subnet.public[*].id
}

output "pipeline_task_security_group_id" {
  description = "Security group ID (outbound only) attached to every Fargate task."
  value       = aws_security_group.pipeline_task.id
}

output "budget_name" {
  description = "AWS Budgets cost budget name — notification-only, never auto-disables resources."
  value       = aws_budgets_budget.project_monthly_cost.name
}
