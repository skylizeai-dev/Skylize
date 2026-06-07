# Skylize backend image — one image set; tiers differ by config, not code.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for layer caching.
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

# Copy source, migrations, and infra.
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY infra ./infra

ENV PYTHONPATH=/app/src \
    SKYLIZE_BACKEND=postgres

EXPOSE 8000

# Entrypoint runs migrations then starts the gateway (overridable per service).
CMD ["sh", "-c", "alembic upgrade head && uvicorn skylize.edge.gateway:app --host 0.0.0.0 --port 8000"]
