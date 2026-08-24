#!/bin/bash
# EC2 user-data for the Metabase instance (metabase.tf) — Amazon Linux
# 2023. Installs Docker, then runs Metabase as a container restarted by
# Docker itself (--restart unless-stopped) rather than a systemd unit,
# so a reboot or a Docker daemon restart both bring it back without any
# extra unit file to maintain. Metabase's own app database (dashboards,
# saved questions, users) is its embedded H2 file, persisted on this
# instance's root EBS volume via the bind-mounted /opt/metabase-data —
# it survives reboots/stops but is tied to this one instance (see
# docs/aws/analytics.md for that trade-off; this project deliberately
# does not run a separate RDS database for it).
set -euo pipefail

dnf update -y
dnf install -y docker
systemctl enable --now docker

mkdir -p /opt/metabase-data

# Idempotent across re-runs of user-data (e.g. instance replacement from
# the same launch config): remove any previous container with the same
# name before starting a fresh one.
docker rm -f metabase 2>/dev/null || true

docker run -d \
  --name metabase \
  --restart unless-stopped \
  -p 3000:3000 \
  -e MB_DB_FILE=/metabase-data/metabase.db \
  -e AWS_REGION=${aws_region} \
  -v /opt/metabase-data:/metabase-data \
  metabase/metabase:${metabase_image_tag}
