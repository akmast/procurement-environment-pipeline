# Metabase — the only always-on, persistent, publicly-reachable
# resource this project runs (everything else is a batch Fargate task
# or a managed query service). Runs as a single Docker container on a
# single EC2 instance in a public subnet (network.tf already has no NAT
# Gateway — same MVP trade-off as the pipeline's Fargate tasks), with
# its own security group locked down to var.metabase_allowed_cidr_blocks
# on port 3000 only. See docs/aws/analytics.md for the full picture:
# connecting Metabase to Athena, and the durability trade-off of its
# embedded H2 app database living on this instance's own EBS volume
# instead of a separate RDS database.
data "aws_ami" "amazon_linux_2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_security_group" "metabase" {
  name        = "${local.name_prefix}-metabase"
  description = "Metabase web UI - inbound 3000 restricted to metabase_allowed_cidr_blocks, outbound only otherwise."
  vpc_id      = aws_vpc.pipeline.id

  ingress {
    description = "Metabase web UI"
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = var.metabase_allowed_cidr_blocks
  }

  egress {
    description = "All outbound (Athena/Glue/S3 API calls, Docker image pull, package updates)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.common_tags
}

resource "aws_instance" "metabase" {
  ami                    = data.aws_ami.amazon_linux_2023.id
  instance_type          = var.metabase_instance_type
  subnet_id              = aws_subnet.public[0].id
  vpc_security_group_ids = [aws_security_group.metabase.id]
  iam_instance_profile   = aws_iam_instance_profile.metabase.name

  # No SSH key pair — shell access is via SSM Session Manager
  # (MetabaseInstanceRole carries AmazonSSMManagedInstanceCore, see
  # iam.tf), not an inbound port 22.

  root_block_device {
    volume_size = var.metabase_volume_size_gb
    volume_type = "gp3"
    encrypted   = true
  }

  user_data = templatefile(
    "${path.module}/templates/metabase_user_data.sh.tpl",
    {
      aws_region         = var.aws_region
      metabase_image_tag = var.metabase_image_tag
    }
  )

  # Re-run user_data on a deliberate image-tag bump instead of requiring
  # a manual instance replacement/SSM login to pull the new container.
  user_data_replace_on_change = true

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-metabase" })
}

resource "aws_eip" "metabase" {
  instance = aws_instance.metabase.id
  domain   = "vpc"

  tags = local.common_tags
}
