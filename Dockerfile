# syntax=docker/dockerfile:1.7
# Multi-stage: build deps separately to cache layers; run as non-root.

# ── Stage 1: dependency builder ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install --prefix=/install .

# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    SKYLIZE_BACKEND=postgres \
    PATH="/install/bin:$PATH"

# Install curl for health check; clean apt lists to keep layer small
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy source (non-root-owned)
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY infra ./infra

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
