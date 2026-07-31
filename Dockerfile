# syntax=docker/dockerfile:1.7
# THE gateway image. There is exactly one gateway Dockerfile in this repo.
#
# It used to be two: this file (built by .github/workflows/deploy-staging.yml
# and deploy.ps1) and infra/Dockerfile (built by infra/docker-compose.yml).
# They diverged, so CI validated an image nobody ran and compose ran an image
# CI never validated. infra/Dockerfile was deleted; every consumer now builds
# this file. Do not reintroduce a second gateway Dockerfile.
#
# Multi-stage: build the wheel separately so the runtime layer carries no build
# toolchain; run as non-root.

# -- Stage 1: dependency + package builder -----------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# pyproject.toml AND src/ together. pyproject.toml sets
# [tool.setuptools.packages.find] where = ["src"], so setuptools resolves
# egg_base against src/ and the build aborts with
#   error: error in 'egg_base' option: 'src' does not exist or is not a directory
# if only pyproject.toml is copied. That was this file's state until 2026-07-31
# and it meant the image CI builds had never built at all.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --upgrade pip && \
    pip install --prefix=/install .

# -- Stage 2: runtime image ---------------------------------------------------
FROM python:3.12-slim AS runtime

# NO PYTHONPATH=/app/src HERE, DELIBERATELY. The package is a real install in
# site-packages (verified: 207 modules, identical to src/). Adding /app/src to
# the path would shadow that install with a loose source copy, so a broken
# install would import fine and only surface when PYTHONPATH changed. Import
# the installed package or fail — do not reintroduce the crutch.
# NO PATH=/install/bin EITHER: `COPY --from=builder /install /usr/local` lands
# the console scripts in /usr/local/bin, which is already first on PATH.
# /install does not exist in this stage and never did.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SKYLIZE_BACKEND=postgres

# curl is NOT optional: the ECS task definition's container health check is
# `curl -f http://localhost:8000/health`
# (infra/terraform/staging/modules/ecs/main.tf), as is the HEALTHCHECK below.
# python:3.12-slim ships no curl, so an image without this layer fails its own
# health check forever and the ECS deployment circuit breaker rolls it back.
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Installed packages + console scripts (alembic, uvicorn) from the builder.
COPY --from=builder /install /usr/local

WORKDIR /app

# Migration scripts are NOT part of the wheel (packages.find is scoped to
# src/), and the CMD below runs `alembic upgrade head` from this directory, so
# both of these must be present in the runtime image.
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini

# Create non-root user
RUN groupadd --gid 1001 skylize && \
    useradd --uid 1001 --gid skylize --shell /bin/bash --create-home skylize && \
    chown -R skylize:skylize /app

USER skylize

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Entrypoint: migrate then serve. ECS runs migrations separately via a one-off
# task; this CMD keeps parity with docker-compose local workflow.
CMD ["sh", "-c", "alembic upgrade head && uvicorn skylize.edge.gateway:app --host 0.0.0.0 --port 8000 --workers 1"]
