"""
Integration-test fixtures: REAL Postgres + Redis, no mocks (Sprint-2 Tasks 4/5).

Every fixture here is gated on an environment variable and SKIPS the test when it
is absent, so the default `pytest` run (no infra) stays green while CI's
`integration` job — which provides service containers — actually exercises them:

    SKYLIZE_TEST_DB_URL=postgresql://skylize:localdev@localhost:5432/skylize
    SKYLIZE_TEST_REDIS_URL=redis://localhost:6379

The Postgres fixture applies Alembic migrations to a disposable schema so a run
never pollutes a shared database, and tears it down afterwards.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

DB_URL = os.getenv("SKYLIZE_TEST_DB_URL")           # admin/superuser — migrations
APP_DB_URL = os.getenv("SKYLIZE_TEST_APP_DB_URL")   # non-superuser app role — RLS-subject
REDIS_URL = os.getenv("SKYLIZE_TEST_REDIS_URL")

requires_pg = pytest.mark.skipif(not DB_URL, reason="SKYLIZE_TEST_DB_URL not set")
requires_app_role = pytest.mark.skipif(
    not (DB_URL and APP_DB_URL),
    reason="SKYLIZE_TEST_DB_URL and SKYLIZE_TEST_APP_DB_URL must both be set",
)
requires_redis = pytest.mark.skipif(not REDIS_URL, reason="SKYLIZE_TEST_REDIS_URL not set")


@pytest_asyncio.fixture()
async def pg_schema() -> AsyncIterator[str]:
    """Create a throwaway schema, run migrations into it, drop it after.

    Yields the schema name. Migrations target this schema via search_path so the
    RLS policies, append-only trigger, and tables all land in an isolated space.
    """
    if not DB_URL:
        pytest.skip("SKYLIZE_TEST_DB_URL not set")

    import asyncpg
    from alembic import command
    from alembic.config import Config

    schema = f"test_{uuid.uuid4().hex[:12]}"
    conn = await asyncpg.connect(DB_URL)
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    await conn.close()

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", DB_URL)
    # Migrations honor this so DDL lands in the disposable schema.
    cfg.set_main_option("version_table_schema", schema)
    os.environ["SKYLIZE_TEST_SCHEMA"] = schema
    try:
        command.upgrade(cfg, "head")
        yield schema
    finally:
        conn = await asyncpg.connect(DB_URL)
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await conn.close()
        os.environ.pop("SKYLIZE_TEST_SCHEMA", None)


@pytest_asyncio.fixture()
async def migrated_public() -> AsyncIterator[None]:
    """Ensure migrations are applied to the real `public` schema (admin role).

    Used by the app-role RLS tests, which must run against the same schema the
    `skylize_app` role was granted on. CI also runs `alembic upgrade head`
    beforehand; this fixture makes the dependency explicit and idempotent.
    """
    if not DB_URL:
        pytest.skip("SKYLIZE_TEST_DB_URL not set")

    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", DB_URL)
    command.upgrade(cfg, "head")
    yield


@pytest_asyncio.fixture()
async def app_conn(migrated_public: None) -> AsyncIterator["object"]:
    """A connection as the NON-SUPERUSER `skylize_app` role (subject to RLS).

    This is the connection that proves tenant isolation actually holds: a
    superuser would bypass RLS, so isolation must be demonstrated as this role.
    """
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")

    import asyncpg

    conn = await asyncpg.connect(APP_DB_URL)
    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture()
async def admin_conn() -> AsyncIterator["object"]:
    """A connection as the admin/superuser role (seeds tenants, asserts role attrs)."""
    if not DB_URL:
        pytest.skip("SKYLIZE_TEST_DB_URL not set")

    import asyncpg

    conn = await asyncpg.connect(DB_URL)
    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture()
async def redis_client() -> AsyncIterator["object"]:
    """A flushed, isolated Redis client; FLUSHDB before and after."""
    if not REDIS_URL:
        pytest.skip("SKYLIZE_TEST_REDIS_URL not set")

    import redis.asyncio as redis

    client = redis.from_url(REDIS_URL, decode_responses=True)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()
