# MVP network: public subnets + an Internet Gateway, no NAT Gateway.
#
# Trade-off (see docs/aws/architecture.md for the full writeup): Fargate
# tasks are short-lived batch jobs (RunTask — start, run, exit) that need
# outbound HTTPS to the EEA/TED/Eurostat APIs and to AWS service
# endpoints (S3, ECR, CloudWatch Logs), and never accept any inbound
# connection. A NAT Gateway would let tasks sit in private subnets with
# no public IP, but costs ~$0.045/hour (~$32/month) plus per-GB data
# processing charges, running whether or not a task is active. Public
# subnets with `assign_public_ip = true` and a security group with zero
# inbound rules get the same outbound-only capability for free — the
# only difference is the task's ENI briefly holds a public IP for the
# few minutes it runs, which is safe precisely because nothing is
# listening on it. Revisit this if inbound connectivity (e.g. a
# long-running service) is ever needed.
resource "aws_vpc" "pipeline" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-vpc" })
}

resource "aws_internet_gateway" "pipeline" {
  vpc_id = aws_vpc.pipeline.id

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-igw" })
}

resource "aws_subnet" "public" {
  count = length(var.public_subnet_cidrs)

  vpc_id                  = aws_vpc.pipeline.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-public-${count.index}" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.pipeline.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.pipeline.id
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-public-rt" })
}

resource "aws_route_table_association" "public" {
  count = length(aws_subnet.public)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "pipeline_task" {
  name        = "${local.name_prefix}-task"
  description = "Fargate pipeline task security group - outbound only, no inbound rules at all."
  vpc_id      = aws_vpc.pipeline.id

  egress {
    description = "All outbound (source APIs + AWS service endpoints)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.common_tags
}
