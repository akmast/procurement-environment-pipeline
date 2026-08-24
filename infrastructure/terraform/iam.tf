# Four separate runtime roles, least-privilege and single-purpose — none
# of them is the deployment role. GitHubDeployRole (assumed by GitHub
# Actions via OIDC) is used only to run `terraform apply`/push images/
# call StartExecution; the container itself runs under PipelineTaskRole,
# never under GitHubDeployRole.

# --------------------------------------------------------------------------
# EcsTaskExecutionRole — pulls the image from ECR and ships container
# logs to CloudWatch. Standard AWS-managed policy covers exactly this.
# --------------------------------------------------------------------------
resource "aws_iam_role" "ecs_task_execution" {
  name = "EcsTaskExecutionRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ecs-tasks.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_managed" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# --------------------------------------------------------------------------
# PipelineTaskRole — the container's own runtime identity (ECS task role,
# not execution role). Scoped to exactly what main.py's own AWS calls
# need: read/write the pipeline data bucket via common/storage.py. No
# other AWS API is called at runtime — outbound HTTPS to the source APIs
# needs network egress (see network.tf), not IAM permissions.
# --------------------------------------------------------------------------
resource "aws_iam_role" "pipeline_task" {
  name = "PipelineTaskRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ecs-tasks.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "pipeline_task" {
  name = "pipeline-data-bucket-access"
  role = aws_iam_role.pipeline_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListDataBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.pipeline_data.arn]
      },
      {
        Sid    = "ReadWriteDataBucketObjects"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
        ]
        Resource = ["${aws_s3_bucket.pipeline_data.arn}/*"]
      }
    ]
  })
}

# --------------------------------------------------------------------------
# StepFunctionsRole — assumed by every state machine. Needs ecs:RunTask
# (+ Stop/Describe for the .sync pattern), iam:PassRole limited to
# exactly the two ECS roles above, and the CloudWatch Logs "vended logs"
# permissions AWS documents for Step Functions execution logging.
# --------------------------------------------------------------------------
resource "aws_iam_role" "step_functions" {
  name = "StepFunctionsRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "states.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "step_functions" {
  name = "run-pipeline-ecs-tasks"
  role = aws_iam_role.step_functions.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RunEcsTasks"
        Effect = "Allow"
        Action = [
          "ecs:RunTask",
          "ecs:StopTask",
          "ecs:DescribeTasks",
          # Required because every RunTask call sets PropagateTags=TASK_DEFINITION
          # (see step_functions.tf's templates) so each running task — and
          # therefore its Fargate compute cost — carries the task
          # definition's "Project" tag; that's what budget.tf's cost
          # budget filters on. Without this permission, tag propagation
          # on RunTask fails.
          "ecs:TagResource",
        ]
        Resource = [
          aws_ecs_task_definition.pipeline.arn,
          # RunTask with .sync also needs to describe/stop tasks it started,
          # which are addressed by task ARN (cluster-scoped), not the task
          # definition ARN above.
          "arn:aws:ecs:${var.aws_region}:${var.aws_account_id}:task/${aws_ecs_cluster.pipeline.name}/*",
        ]
      },
      {
        Sid    = "PassEcsRoles"
        Effect = "Allow"
        Action = "iam:PassRole"
        Resource = [
          aws_iam_role.ecs_task_execution.arn,
          aws_iam_role.pipeline_task.arn,
        ]
      },
      {
        # Required by the ECS RunTask.sync (and Lambda/other .sync)
        # integration pattern — Step Functions manages an internal
        # EventBridge rule to know when the ECS task completes.
        Sid    = "SyncIntegrationEventRule"
        Effect = "Allow"
        Action = [
          "events:PutTargets",
          "events:PutRule",
          "events:DescribeRule",
        ]
        Resource = [
          "arn:aws:events:${var.aws_region}:${var.aws_account_id}:rule/StepFunctionsGetEventsForECSTaskRule",
        ]
      },
      {
        # AWS-documented permissions for Step Functions execution
        # logging to a CloudWatch "vended logs" log group.
        Sid    = "ExecutionLogging"
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups",
        ]
        Resource = ["*"]
      }
    ]
  })
}

# --------------------------------------------------------------------------
# SchedulerRole — assumed by EventBridge Scheduler solely to start
# UpdateStateMachine on its monthly schedule.
# --------------------------------------------------------------------------
resource "aws_iam_role" "scheduler" {
  name = "SchedulerRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "scheduler.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = { "aws:SourceAccount" = var.aws_account_id }
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "scheduler" {
  name = "start-update-state-machine"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "StartUpdateStateMachine"
        Effect   = "Allow"
        Action   = "states:StartExecution"
        Resource = [aws_sfn_state_machine.update.arn]
      }
    ]
  })
}

# --------------------------------------------------------------------------
# GitHubDeployRole — already exists with its OIDC trust policy already
# configured (see docs/aws/deployment.md); this only attaches the
# permissions it needs to deploy exactly this project's resources.
# Terraform does NOT create the role or touch its trust policy.
# --------------------------------------------------------------------------
data "aws_iam_role" "github_deploy" {
  name = var.github_deploy_role_name
}

resource "aws_iam_role_policy" "github_deploy" {
  name = "deploy-procurement-environment-pipeline"
  role = data.aws_iam_role.github_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "TerraformState"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket",
        ]
        Resource = [
          "arn:aws:s3:::procurement-pipeline-tfstate-${var.aws_account_id}",
          "arn:aws:s3:::procurement-pipeline-tfstate-${var.aws_account_id}/*",
        ]
      },
      {
        Sid    = "PipelineDataBucketManage"
        Effect = "Allow"
        Action = [
          "s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket",
          "s3:GetBucket*", "s3:PutBucket*", "s3:GetEncryptionConfiguration",
          "s3:GetLifecycleConfiguration", "s3:PutLifecycleConfiguration",
        ]
        Resource = [
          aws_s3_bucket.pipeline_data.arn,
          "${aws_s3_bucket.pipeline_data.arn}/*",
        ]
      },
      {
        Sid    = "EcrManageAndPush"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
        ]
        Resource = ["*"]
      },
      {
        Sid    = "EcrRepository"
        Effect = "Allow"
        Action = [
          "ecr:DescribeRepositories", "ecr:CreateRepository",
          "ecr:PutLifecyclePolicy", "ecr:PutImageScanningConfiguration",
          "ecr:PutImageTagMutability", "ecr:BatchCheckLayerAvailability",
          "ecr:PutImage", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload", "ecr:BatchGetImage",
        ]
        Resource = [aws_ecr_repository.pipeline.arn]
      },
      {
        Sid    = "EcsManage"
        Effect = "Allow"
        Action = [
          "ecs:DescribeClusters", "ecs:CreateCluster", "ecs:DeleteCluster",
          "ecs:DescribeTaskDefinition", "ecs:RegisterTaskDefinition",
          "ecs:DeregisterTaskDefinition", "ecs:TagResource",
          "ecs:ListTagsForResource",
        ]
        Resource = ["*"]
      },
      {
        Sid    = "PassRuntimeRoles"
        Effect = "Allow"
        Action = "iam:PassRole"
        Resource = [
          aws_iam_role.ecs_task_execution.arn,
          aws_iam_role.pipeline_task.arn,
          aws_iam_role.step_functions.arn,
          aws_iam_role.scheduler.arn,
          aws_iam_role.metabase_instance.arn,
        ]
      },
      {
        Sid    = "ManageRuntimeIamRoles"
        Effect = "Allow"
        Action = [
          "iam:GetRole", "iam:CreateRole", "iam:DeleteRole", "iam:TagRole",
          "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:GetRolePolicy",
          "iam:AttachRolePolicy", "iam:DetachRolePolicy",
          "iam:ListRolePolicies", "iam:ListAttachedRolePolicies",
          "iam:PutRolePermissionsBoundary",
        ]
        Resource = [
          aws_iam_role.ecs_task_execution.arn,
          aws_iam_role.pipeline_task.arn,
          aws_iam_role.step_functions.arn,
          aws_iam_role.scheduler.arn,
          aws_iam_role.metabase_instance.arn,
        ]
      },
      {
        Sid    = "ManageMetabaseInstanceProfile"
        Effect = "Allow"
        Action = [
          "iam:GetInstanceProfile", "iam:CreateInstanceProfile", "iam:DeleteInstanceProfile",
          "iam:AddRoleToInstanceProfile", "iam:RemoveRoleFromInstanceProfile",
          "iam:TagInstanceProfile",
        ]
        Resource = [aws_iam_instance_profile.metabase.arn]
      },
      {
        Sid      = "ManageOwnDeployPolicy"
        Effect   = "Allow"
        Action   = ["iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy"]
        Resource = [data.aws_iam_role.github_deploy.arn]
      },
      {
        Sid    = "ManageStateMachines"
        Effect = "Allow"
        Action = [
          "states:CreateStateMachine", "states:UpdateStateMachine",
          "states:DeleteStateMachine", "states:DescribeStateMachine",
          "states:TagResource", "states:ListTagsForResource",
          "states:StartExecution",
        ]
        Resource = ["*"]
      },
      {
        Sid    = "ManageScheduler"
        Effect = "Allow"
        Action = [
          "scheduler:CreateSchedule", "scheduler:UpdateSchedule",
          "scheduler:DeleteSchedule", "scheduler:GetSchedule", "scheduler:TagResource",
        ]
        Resource = ["*"]
      },
      {
        # AWS Budgets uses a coarse-grained permission model — ViewBudget
        # covers reads/describes, ModifyBudget covers create/update/delete
        # (see budget.tf). Missed when budget.tf was first added; this is
        # what "AccessDeniedException ... budgets:ModifyBudget" means.
        Sid    = "ManageBudget"
        Effect = "Allow"
        Action = [
          "budgets:ViewBudget",
          "budgets:ModifyBudget",
        ]
        Resource = ["arn:aws:budgets::${var.aws_account_id}:budget/*"]
      },
      {
        Sid    = "ManageNetworking"
        Effect = "Allow"
        Action = [
          "ec2:DescribeVpcs", "ec2:CreateVpc", "ec2:DeleteVpc", "ec2:ModifyVpcAttribute",
          "ec2:DescribeSubnets", "ec2:CreateSubnet", "ec2:DeleteSubnet",
          "ec2:DescribeInternetGateways", "ec2:CreateInternetGateway",
          "ec2:DeleteInternetGateway", "ec2:AttachInternetGateway", "ec2:DetachInternetGateway",
          "ec2:DescribeRouteTables", "ec2:CreateRouteTable", "ec2:DeleteRouteTable",
          "ec2:CreateRoute", "ec2:DeleteRoute", "ec2:AssociateRouteTable",
          "ec2:DisassociateRouteTable",
          "ec2:DescribeSecurityGroups", "ec2:CreateSecurityGroup", "ec2:DeleteSecurityGroup",
          "ec2:AuthorizeSecurityGroupEgress", "ec2:RevokeSecurityGroupEgress",
          "ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress",
          "ec2:CreateTags", "ec2:DeleteTags", "ec2:DescribeTags",
        ]
        Resource = ["*"]
      },
      {
        # Metabase instance (metabase.tf): the instance itself, its
        # Elastic IP, and the AMI lookup used to find the latest Amazon
        # Linux 2023 image.
        Sid    = "ManageMetabaseInstance"
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances", "ec2:RunInstances", "ec2:TerminateInstances",
          "ec2:StopInstances", "ec2:StartInstances", "ec2:ModifyInstanceAttribute",
          "ec2:DescribeInstanceAttribute", "ec2:DescribeInstanceTypes",
          "ec2:DescribeImages",
          "ec2:DescribeVolumes", "ec2:CreateVolume", "ec2:DeleteVolume", "ec2:ModifyVolume",
          "ec2:DescribeAddresses", "ec2:AllocateAddress", "ec2:ReleaseAddress",
          "ec2:AssociateAddress", "ec2:DisassociateAddress",
          "ec2:DescribeIamInstanceProfileAssociations", "ec2:AssociateIamInstanceProfile",
          "ec2:DisassociateIamInstanceProfile", "ec2:ReplaceIamInstanceProfileAssociation",
          "ec2:DescribeKeyPairs",
        ]
        Resource = ["*"]
      },
      {
        # Glue Catalog (glue.tf) — database + the three Gold tables.
        Sid    = "ManageGlueCatalog"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase", "glue:CreateDatabase", "glue:UpdateDatabase", "glue:DeleteDatabase",
          "glue:GetTable", "glue:GetTables", "glue:CreateTable", "glue:UpdateTable", "glue:DeleteTable",
          "glue:TagResource", "glue:UntagResource", "glue:GetTags",
        ]
        Resource = [
          "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:catalog",
          aws_glue_catalog_database.gold.arn,
          "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:table/${aws_glue_catalog_database.gold.name}/*",
        ]
      },
      {
        # Athena workgroup (athena.tf).
        Sid    = "ManageAthenaWorkgroup"
        Effect = "Allow"
        Action = [
          "athena:GetWorkGroup", "athena:CreateWorkGroup", "athena:UpdateWorkGroup",
          "athena:DeleteWorkGroup", "athena:TagResource", "athena:ListTagsForResource",
        ]
        Resource = [aws_athena_workgroup.gold.arn]
      },
      {
        Sid    = "ManageLogGroups"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup", "logs:DeleteLogGroup", "logs:PutRetentionPolicy",
          "logs:DescribeLogGroups", "logs:TagResource", "logs:ListTagsForResource",
        ]
        Resource = ["*"]
      },
      {
        Sid      = "VerifyIdentity"
        Effect   = "Allow"
        Action   = "sts:GetCallerIdentity"
        Resource = "*"
      }
    ]
  })
}

# --------------------------------------------------------------------------
# MetabaseInstanceRole — the Metabase EC2 instance's own runtime identity
# (instance profile, see metabase.tf). Scoped to exactly what Metabase
# needs to run Athena queries against the Gold Layer: submit/read Athena
# queries, read the Glue Catalog (glue.tf), read the Gold data under S3
# plus read/write the Athena query-results prefix (athena.tf). Also
# carries SSM's managed-instance policy so the instance is reachable for
# shell access via SSM Session Manager rather than an open SSH port — the
# security group (metabase.tf) has no inbound rule for port 22 at all.
# --------------------------------------------------------------------------
resource "aws_iam_role" "metabase_instance" {
  name = "MetabaseInstanceRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ec2.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "metabase_instance_ssm" {
  role       = aws_iam_role.metabase_instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "metabase_instance" {
  name = "metabase-athena-glue-gold-access"
  role = aws_iam_role.metabase_instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RunAthenaQueries"
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:StopQueryExecution",
          "athena:GetWorkGroup",
          "athena:ListQueryExecutions",
        ]
        Resource = [aws_athena_workgroup.gold.arn]
      },
      {
        Sid    = "ReadGlueCatalog"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase",
          "glue:GetDatabases",
          "glue:GetTable",
          "glue:GetTables",
          "glue:GetPartition",
          "glue:GetPartitions",
        ]
        Resource = [
          "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:catalog",
          aws_glue_catalog_database.gold.arn,
          "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:table/${aws_glue_catalog_database.gold.name}/*",
        ]
      },
      {
        Sid    = "ReadGoldData"
        Effect = "Allow"
        Action = ["s3:GetObject"]
        Resource = [
          "${aws_s3_bucket.pipeline_data.arn}/data/gold/*",
        ]
      },
      {
        Sid      = "ListDataBucketForAthena"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.pipeline_data.arn]
        Condition = {
          StringLike = {
            "s3:prefix" = ["data/gold/*", "athena-results/*"]
          }
        }
      },
      {
        Sid      = "ReadWriteAthenaResults"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = ["${aws_s3_bucket.pipeline_data.arn}/athena-results/*"]
      }
    ]
  })
}

resource "aws_iam_instance_profile" "metabase" {
  name = "MetabaseInstanceProfile"
  role = aws_iam_role.metabase_instance.name

  tags = local.common_tags
}
