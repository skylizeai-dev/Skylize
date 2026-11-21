"""Migration 0038 (`user_refresh_tokens.family_id`) on PRE-EXISTING data, real Postgres.

What only a database can show:
  * rows written BEFORE 0038 (the 0037 table shape, no family column) each get a
    well-defined family after the upgrade, their own `token_id`, so no two
    sessions are merged, and every other column is byte-for-byte unchanged;
  * those pre-migration tokens still rotate through the real `PgUserRepository`
    afterwards, and a rotation continues their family;
  * app code from before 0038 (an INSERT that omits the column) keeps working
    during a rolling deploy, and gets a family of its own;
  * NULL can never be stored, so no reader has to interpret a NULL family;
  * grants (the table ACL) and RLS (none) are exactly as before;
  * downgrade drops only what upgrade added and keeps every row; a second
    upgrade backfills again.

WHY A DISPOSABLE SCHEMA, NOT `public`. A downgrade of `public` would remove the
column every other integration test's app code writes, and CI runs the whole
integration job against that one shared `public` schema. So each test here
creates its own schema, migrates it to 0037 (the revision BEFORE this one),
seeds it in the old shape, then drives the upgrade / downgrade itself, and
drops the schema at the end; `public` is never touched. Revisions are named
explicitly ("0037", "0038") rather than "head", so a later migration cannot
change what this file proves. The DSN and schema plumbing mirror
`conftest.pg_schema` / `_upgrade_head` (migrations/env.py honours
SKYLIZE_TEST_SCHEMA and re-derives the DSN from SKYLIZE_DB_URL).

Connections here use the admin DSN; the table has no RLS, and the app role's
use of the new column is proven in `public` by test_refresh_revocation_pg.py,
whose container connects as `skylize_app`.

Skipped unless SKYLIZE_TEST_DB_URL is set.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import pytest_asyncio

from skylize.dal.users import PgUserRepository

from .conftest import DB_URL, requires_pg

pytestmark = pytest.mark.integration

_BEFORE, _THIS = "0037", "0038"
_APP_ROLE = "skylize_app"


def _alembic(action: str, revision: str, schema: str) -> None:
    """`alembic upgrade|downgrade <revision>` into `schema`. Runs in a worker
    thread (env.py calls asyncio.run). Env vars are set for the call only."""
    from alembic import command
    from alembic.config import Config

    assert DB_URL is not None
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", DB_URL)
    cfg.set_main_option("version_table_schema", schema)
    saved = {k: os.environ.get(k) for k in ("SKYLIZE_DB_URL", "SKYLIZE_TEST_SCHEMA")}
    os.environ["SKYLIZE_DB_URL"] = DB_URL
    os.environ["SKYLIZE_TEST_SCHEMA"] = schema
    try:
        getattr(command, action)(cfg, revision)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def _migrate(action: str, revision: str, schema: str) -> None:
    await asyncio.to_thread(_alembic, action, revision, schema)


@pytest_asyncio.fixture()
async def schema_at_0037() -> AsyncIterator[str]:
    """A throwaway schema migrated to 0037, i.e. the table as it is before 0038."""
    if not DB_URL:
        pytest.skip("SKYLIZE_TEST_DB_URL not set")
    import asyncpg

    schema = f"test_fam_{uuid.uuid4().hex[:10]}"
    conn = await asyncpg.connect(DB_URL)
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    await conn.close()
    try:
        await _migrate("upgrade", _BEFORE, schema)
        yield schema
    finally:
        conn = await asyncpg.connect(DB_URL)
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await conn.close()


@asynccontextmanager
async def _schema_conn(schema: str) -> AsyncIterator[Any]:
    import asyncpg

    conn = await asyncpg.connect(DB_URL, server_settings={"search_path": f'"{schema}", public'})
    try:
        yield conn
    finally:
        await conn.close()


class _SchemaDb:
    """Just enough of `dal.connection.Database` for `PgUserRepository`
    (`admin_session()` only), on a pool whose search_path is the schema."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @asynccontextmanager
    async def admin_session(self) -> AsyncIterator[Any]:
        async with self._pool.acquire() as conn:
            yield conn


@asynccontextmanager
async def _schema_repo(schema: str) -> AsyncIterator[PgUserRepository]:
    import asyncpg

    pool = await asyncpg.create_pool(
        DB_URL, min_size=1, max_size=2, server_settings={"search_path": f'"{schema}", public'}
    )
    try:
        yield PgUserRepository(_SchemaDb(pool))  # type: ignore[arg-type]
    finally:
        await pool.close()


async def _seed_pre_0038(conn: Any) -> dict[str, uuid.UUID]:
    """One user and three refresh rows in the 0037 shape (no family column):
    two live tokens of two sessions, and one consumed an hour ago."""
    org = f"fam_mig_{uuid.uuid4().hex[:8]}"
    user_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$1,$2)",
        org, "https://issuer.example",
    )
    await conn.execute(
        "INSERT INTO users (user_id, org_id, email, password_hash, roles) "
        "VALUES ($1,$2,$3,'x',ARRAY['owner'])",
        user_id, org, f"{org}@example.com",
    )
    ids = {"user": user_id, "live_a": uuid.uuid4(), "live_b": uuid.uuid4(), "consumed": uuid.uuid4()}
    later = datetime.now(timezone.utc) + timedelta(days=7)
    for name in ("live_a", "live_b"):
        await conn.execute(
            "INSERT INTO user_refresh_tokens (token_id, user_id, expires_at) VALUES ($1,$2,$3)",
            ids[name], user_id, later,
        )
    await conn.execute(
        "INSERT INTO user_refresh_tokens (token_id, user_id, expires_at, revoked_at) "
        "VALUES ($1,$2,$3, now() - interval '1 hour')",
        ids["consumed"], user_id, later,
    )
    return ids


_SNAPSHOT_SQL = (
    "SELECT token_id, user_id, expires_at, revoked_at, created_at "
    "FROM user_refresh_tokens ORDER BY token_id"
)


async def _snapshot(conn: Any) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in await conn.fetch(_SNAPSHOT_SQL)]


async def _table_acl_and_rls(conn: Any, schema: str) -> tuple[Any, bool, bool]:
    rec = await conn.fetchrow(
        "SELECT c.relacl::text AS acl, c.relrowsecurity, c.relforcerowsecurity "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = $1 AND c.relname = 'user_refresh_tokens'",
        schema,
    )
    assert rec is not None
    return rec["acl"], rec["relrowsecurity"], rec["relforcerowsecurity"]


async def _family_column(conn: Any, schema: str) -> Any:
    return await conn.fetchrow(
        "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = $1 AND table_name = 'user_refresh_tokens' "
        "AND column_name = 'family_id'",
        schema,
    )


async def _family_index(conn: Any, schema: str) -> Any:
    return await conn.fetchval(
        "SELECT indexdef FROM pg_indexes "
        "WHERE schemaname = $1 AND indexname = 'idx_refresh_tokens_family_live'",
        schema,
    )


@requires_pg
async def test_pg_migration_0038_gives_every_existing_token_its_own_family_and_it_still_refreshes(
    schema_at_0037: str,
) -> None:
    import asyncpg

    schema = schema_at_0037
    async with _schema_conn(schema) as conn:
        assert await _family_column(conn, schema) is None, "0037 already has family_id?"
        ids = await _seed_pre_0038(conn)
        rows_before = await _snapshot(conn)
        acl_before = await _table_acl_and_rls(conn, schema)

    await _migrate("upgrade", _THIS, schema)

    async with _schema_conn(schema) as conn:
        # The column: UUID, NOT NULL, random default for old-code inserts.
        col = await _family_column(conn, schema)
        assert col is not None
        assert (col["data_type"], col["is_nullable"]) == ("uuid", "NO"), dict(col)
        assert "gen_random_uuid()" in (col["column_default"] or ""), dict(col)
        index = await _family_index(conn, schema)
        assert index is not None and "(family_id)" in index and "revoked_at IS NULL" in index, index

        # Existing data: unchanged in every old column, and each row is the root
        # of its OWN family - three rows, three distinct families.
        assert await _snapshot(conn) == rows_before
        families = {
            r["token_id"]: r["family_id"]
            for r in await conn.fetch("SELECT token_id, family_id FROM user_refresh_tokens")
        }
        assert families == {
            ids["live_a"]: ids["live_a"],
            ids["live_b"]: ids["live_b"],
            ids["consumed"]: ids["consumed"],
        }

        # Grants and RLS exactly as before; the app role reaches the new column
        # through its table-level grant.
        assert await _table_acl_and_rls(conn, schema) == acl_before
        assert acl_before[1] is False, "user_refresh_tokens unexpectedly has RLS"
        qualified = f'"{schema}".user_refresh_tokens'
        for privilege in ("SELECT", "INSERT", "UPDATE"):
            assert await conn.fetchval(
                "SELECT has_column_privilege($1::name, $2::text, 'family_id'::text, $3::text)",
                _APP_ROLE, qualified, privilege,
            ), privilege

        # Rolling deploy: pre-0038 app code inserts WITHOUT the column. It must
        # succeed, and the row gets a family of its own.
        old_code_row = uuid.uuid4()
        await conn.execute(
            "INSERT INTO user_refresh_tokens (token_id, user_id, expires_at) VALUES ($1,$2,$3)",
            old_code_row, ids["user"], datetime.now(timezone.utc) + timedelta(days=7),
        )
        old_family = await conn.fetchval(
            "SELECT family_id FROM user_refresh_tokens WHERE token_id = $1", old_code_row
        )
        assert old_family is not None and old_family not in families.values()

        # NULL can never be stored.
        with pytest.raises(asyncpg.NotNullViolationError):
            await conn.execute(
                "INSERT INTO user_refresh_tokens (token_id, user_id, expires_at, family_id) "
                "VALUES ($1,$2,$3,NULL)",
                uuid.uuid4(), ids["user"], datetime.now(timezone.utc) + timedelta(days=7),
            )

    # The pre-migration tokens still refresh through the REAL repository, and a
    # rotation continues their own family; the consumed one stays refused.
    later = datetime.now(timezone.utc) + timedelta(days=7)
    async with _schema_repo(schema) as repo:
        children = {}
        for name in ("live_a", "live_b"):
            children[name] = uuid.uuid4()
            assert await repo.rotate_refresh_token(
                old_token_id=ids[name], user_id=ids["user"],
                new_token_id=children[name], new_expires_at=later,
            ), name
        old_child = uuid.uuid4()
        assert await repo.rotate_refresh_token(
            old_token_id=old_code_row, user_id=ids["user"],
            new_token_id=old_child, new_expires_at=later,
        )
        assert not await repo.rotate_refresh_token(
            old_token_id=ids["consumed"], user_id=ids["user"],
            new_token_id=uuid.uuid4(), new_expires_at=later,
        )
        for name, child in children.items():
            row = await repo.get_refresh_token(child)
            assert row is not None and row.family_id == ids[name], name
        old_child_row = await repo.get_refresh_token(old_child)
        assert old_child_row is not None and old_child_row.family_id == old_family

        # A login after the migration (new code) roots a new family at itself.
        login = uuid.uuid4()
        await repo.store_refresh_token(login, ids["user"], later)
        login_row = await repo.get_refresh_token(login)
        assert login_row is not None and login_row.family_id == login

        # And a family revocation of one pre-migration session touches only it.
        assert await repo.revoke_family(ids["live_a"]) == 1
        for child in (children["live_b"], old_child, login):
            row = await repo.get_refresh_token(child)
            assert row is not None and row.revoked_at is None
        assert await repo.revoke_family(ids["live_a"]) == 0


@requires_pg
async def test_pg_migration_0038_downgrade_drops_only_what_upgrade_added(
    schema_at_0037: str,
) -> None:
    schema = schema_at_0037
    async with _schema_conn(schema) as conn:
        ids = await _seed_pre_0038(conn)
        acl_before = await _table_acl_and_rls(conn, schema)
        columns_before = await conn.fetch(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = 'user_refresh_tokens' "
            "ORDER BY ordinal_position",
            schema,
        )

    await _migrate("upgrade", _THIS, schema)
    # A row written by the NEW code under 0038 (a rotation, family copied).
    child = uuid.uuid4()
    async with _schema_repo(schema) as repo:
        assert await repo.rotate_refresh_token(
            old_token_id=ids["live_a"], user_id=ids["user"], new_token_id=child,
            new_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
    async with _schema_conn(schema) as conn:
        rows_at_0038 = await _snapshot(conn)

    await _migrate("downgrade", _BEFORE, schema)

    async with _schema_conn(schema) as conn:
        assert await conn.fetchval(f'SELECT version_num FROM "{schema}".alembic_version') == _BEFORE
        assert await _family_column(conn, schema) is None
        assert await _family_index(conn, schema) is None
        columns_after = await conn.fetch(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = 'user_refresh_tokens' "
            "ORDER BY ordinal_position",
            schema,
        )
        assert [tuple(c) for c in columns_after] == [tuple(c) for c in columns_before]
        assert await _table_acl_and_rls(conn, schema) == acl_before
        # Every row kept, the one written under 0038 included.
        assert await _snapshot(conn) == rows_at_0038
        assert len(rows_at_0038) == 4

    # Upgrading again backfills again: every row, the post-0038 child included,
    # is the root of its own family (the downgrade dropped the lineage; it can
    # only come back narrower, never merged).
    await _migrate("upgrade", _THIS, schema)
    async with _schema_conn(schema) as conn:
        mismatched = await conn.fetchval(
            "SELECT count(*) FROM user_refresh_tokens WHERE family_id <> token_id"
        )
        assert mismatched == 0
