# Single image for every stage of every source — the container command is
# overridden per stage by the caller (ECS task definition, or a plain
# `docker run <image> stage --source ... --stage ...`); nothing in the
# image itself is stage-specific. See main.py for the CLI this image runs.
FROM python:3.11-slim

# uv itself ships as a static binary — copied in rather than pip-installed,
# per Astral's documented pattern, so it's available without needing a
# network install step at build time.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, installed from the committed lockfile (--frozen: fail
# rather than silently re-resolve if pyproject.toml/uv.lock ever drift) and
# before the rest of the source is copied in, so an ordinary code change
# doesn't invalidate this layer and re-trigger a full dependency install.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}"

# Runs as an unprivileged user — this container only ever needs outbound
# HTTPS to the source APIs and read/write access to its own S3 bucket via
# PipelineTaskRole, never root.
RUN useradd --create-home --uid 1000 pipeline \
    && chown -R pipeline:pipeline /app
USER pipeline

ENTRYPOINT ["python", "main.py"]
# No subcommand by default — ECS task definitions and manual `docker run`
# invocations always override this with the real command
# (e.g. ["stage", "--source", "eea-measurements", "--stage", "ingestion", ...]).
CMD ["--help"]
