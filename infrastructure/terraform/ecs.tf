resource "aws_ecs_cluster" "pipeline" {
  name = local.name_prefix

  setting {
    name  = "containerInsights"
    value = "disabled"
  }

  tags = local.common_tags
}

# One reusable task definition for every stage of every source — the
# actual command is supplied per-invocation via ECS RunTask's
# containerOverrides (see step_functions.tf and
# docs/aws/architecture.md). Never a persistent ECS Service: every
# invocation is a standalone RunTask call that starts, runs main.py to
# completion, and exits.
resource "aws_ecs_task_definition" "pipeline" {
  family                   = local.name_prefix
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.fargate_cpu
  memory                   = var.fargate_memory

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  ephemeral_storage {
    size_in_gib = var.fargate_ephemeral_storage_gb
  }

  execution_role_arn = aws_iam_role.ecs_task_execution.arn
  task_role_arn      = aws_iam_role.pipeline_task.arn

  container_definitions = jsonencode([
    {
      name  = local.container_name
      image = local.container_image_uri

      environment = [
        { name = "PIPELINE_S3_BUCKET", value = var.data_bucket_name },
        { name = "AWS_DEFAULT_REGION", value = var.aws_region },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "pipeline"
        }
      }
    }
  ])

  tags = local.common_tags
}
