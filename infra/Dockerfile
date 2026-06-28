# Skylize backend image — one image set; tiers differ by config, not code.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copy pyproject.toml AND src/ together — setuptools needs src/ to build the wheel
COPY pyproject.toml ./
COPY src ./src

RUN pip install --upgrade pip && pip install .

# Copy remaining files
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY infra ./infra

ENV PYTHONPATH=/app/src \
    SKYLIZE_BACKEND=postgres

EXPOSE 8000

CMD ["uvicorn", "skylize.edge.gateway:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "debug"]
