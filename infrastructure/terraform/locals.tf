locals {
  name_prefix = var.project_name

  common_tags = {
    Project = var.project_name
  }

  ecr_repository_url  = "${var.aws_account_id}.dkr.ecr.${var.aws_region}.amazonaws.com/${var.ecr_repository_name}"
  container_image_uri = "${local.ecr_repository_url}:${var.container_image_tag}"

  ecs_log_group_name            = "/ecs/${var.project_name}"
  step_functions_log_group_name = "/aws/vendedlogs/states/${var.project_name}"

  container_name = "pipeline"
}
