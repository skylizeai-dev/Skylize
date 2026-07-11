"""
Alembic environment — async (asyncpg) runner.

Migrations are hand-written raw-SQL (`op.execute`) so RLS policies, partial
indexes, and triggers are expressed faithfully; autogenerate is intentionally
not used, so `target_metadata` is None. The DSN comes from the environment.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None  # raw-SQL migrations; no autogenerate


def _dsn() -> str:
    raw = (
        os.environ.get("SKYLIZE_DB_URL")
        or os.environ.get("POSTGRES_DSN")
        or "postgresql://skylize:localdev@localhost:5432/skylize"
    )
    # Force the asyncpg driver (the only Postgres driver we depend on).
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw


def run_migrations_offline() -> None:
    context.configure(
        url=_dsn(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    # Integration tests run migrations into a disposable schema (SKYLIZE_TEST_SCHEMA)
    # so a test run never pollutes a shared database. No effect in normal runs.
    test_schema = os.environ.get("SKYLIZE_TEST_SCHEMA")
    version_table_schema = None
    if test_schema:
        from sqlalchemy import text

        # `public` stays on the path so extension functions installed there
        # (pgcrypto's digest(), etc.) resolve; DDL still lands in test_schema
        # because it comes first.
        connection.execute(text(f'SET search_path TO "{test_schema}", public'))
        version_table_schema = test_schema

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=version_table_schema,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _dsn()
    engine = async_engine_from_config(cfg, prefix="sqlalchemy.")
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
        # The SET search_path call above starts connection's implicit
        # transaction before Alembic's own per-migration transactions do,
        # so nothing is durably committed until this connection-level
        # transaction itself commits — without it, __aexit__ rolls back
        # silently and every migration's DDL vanishes on connection close.
        await connection.commit()
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
