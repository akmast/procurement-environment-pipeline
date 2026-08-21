variable "aws_region" {
  description = "AWS region every resource is created in."
  type        = string
  default     = "eu-central-1"
}

variable "aws_account_id" {
  description = "AWS account ID resources are deployed into — used to build globally-unique names (S3 bucket names) and ARNs."
  type        = string
  default     = "137307166874"
}

variable "project_name" {
  description = "Short name used as a prefix/tag on every resource this project creates."
  type        = string
  default     = "procurement-environment-pipeline"
}

variable "data_bucket_name" {
  description = "S3 bucket holding raw/normalized/transformed data, per-run manifests (runs/), and the bootstrap completion marker (system/bootstrap/)."
  type        = string
  default     = "procurement-pipeline-137307166874-eu-central-1"
}

variable "ecr_repository_name" {
  description = "ECR repository the pipeline's Docker image is pushed to."
  type        = string
  default     = "procurement-environment-pipeline"
}

variable "container_image_tag" {
  description = "Git commit SHA (e.g. \"sha-abc1234\") of the pipeline Docker image to deploy. deploy.yml always passes this explicitly via -var after building and pushing that exact image — the default here only exists so `terraform validate`/`plan` don't hard-fail before any image has ever been pushed."
  type        = string
  default     = "bootstrap"
}

variable "github_owner" {
  description = "GitHub organization/user that owns the repository — informational tagging only; the actual OIDC trust policy on GitHubDeployRole is managed outside this Terraform (already configured, see docs/aws/deployment.md)."
  type        = string
  default     = "akmast"
}

variable "github_repo" {
  description = "GitHub repository name — informational tagging only, same note as github_owner."
  type        = string
  default     = "procurement-environment-pipeline"
}

variable "github_deploy_role_name" {
  description = "Name of the pre-existing IAM role GitHub Actions assumes via OIDC. This Terraform attaches a scoped deployment policy to it but does not create the role or its OIDC trust policy — both are already configured (see docs/aws/deployment.md)."
  type        = string
  default     = "GitHubDeployRole"
}

variable "fargate_cpu" {
  description = "Fargate task vCPU units (256 = 0.25 vCPU, 1024 = 1 vCPU, ...) — must be one of AWS Fargate's valid CPU/memory combinations for the chosen fargate_memory."
  type        = number
  default     = 1024
}

variable "fargate_memory" {
  description = "Fargate task memory, in MiB — must be one of AWS Fargate's valid CPU/memory combinations for the chosen fargate_cpu."
  type        = number
  default     = 4096
}

variable "fargate_ephemeral_storage_gb" {
  description = "Fargate task ephemeral storage, in GiB (20-200; AWS Fargate's minimum/included size is 20)."
  type        = number
  default     = 30
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the pipeline's ECS task and Step Functions execution log groups."
  type        = number
  default     = 90
}

variable "vpc_cidr" {
  description = "CIDR block for the pipeline's VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for the public subnets Fargate tasks run in. Public (not private+NAT Gateway) is a deliberate MVP trade-off — see docs/aws/architecture.md."
  type        = list(string)
  default     = ["10.42.1.0/24", "10.42.2.0/24"]
}

variable "availability_zones" {
  description = "Availability zones the public subnets are created in — must have the same length as public_subnet_cidrs."
  type        = list(string)
  default     = ["eu-central-1a", "eu-central-1b"]
}

variable "update_schedule_expression" {
  description = "EventBridge Scheduler cron expression for the monthly update run — first Monday of the month at 03:00 (evaluated in update_schedule_timezone). The `#` Nth-weekday-of-month qualifier (MON#1) is documented AWS cron syntax shared by EventBridge Scheduler and CloudWatch Events — verify against the current AWS EventBridge Scheduler cron reference before relying on it in production (see docs/aws/operations.md)."
  type        = string
  default     = "cron(0 3 ? * MON#1 *)"
}

variable "update_schedule_timezone" {
  description = "IANA timezone the update schedule's cron expression is evaluated in."
  type        = string
  default     = "Europe/Berlin"
}

variable "update_schedule_enabled" {
  description = "Whether the monthly update schedule is active. Starts DISABLED — enable manually only after a successful manual update run (see docs/aws/operations.md). Never flip this to true as part of routine deploys."
  type        = bool
  default     = false
}

variable "budget_limit_amount" {
  description = "Monthly AWS Budgets cost limit for this project's tagged resources (see budget.tf). Notification-only — never triggers any automatic resource shutdown or restriction."
  type        = number
  default     = 20
}

variable "budget_currency" {
  description = "ISO 4217 currency code for the monthly budget limit, e.g. \"EUR\" or \"USD\". AWS Budgets/Cost Explorer track underlying cost data in USD internally and convert to a non-USD budget currency using AWS's own periodically-updated exchange rate, not necessarily the rate/timing your actual invoice uses — a non-USD budget is a close approximation, not an exact mirror of your bill. Verify your account's real billing currency in Billing Console → Payment preferences before relying on this figure precisely (see docs/aws/operations.md)."
  type        = string
  default     = "EUR"
}

variable "budget_notification_email" {
  description = "Email address that receives budget threshold notifications (see budget.tf's 80%/100% notification blocks). Required, no default — never hardcode an email in Terraform source. Supplied via TF_VAR_budget_notification_email in deploy.yml, sourced from the BUDGET_NOTIFICATION_EMAIL GitHub repo variable; for a local/manual apply, export TF_VAR_budget_notification_email or pass -var."
  type        = string
  sensitive   = true
}
