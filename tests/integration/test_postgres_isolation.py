"""
Postgres integration (Sprint-2 Task 4) — REAL Postgres, no mocks.

Verifies the data-layer guarantees the Sprint-1 audit found untested, AND the
Sprint-2 RLS role fix: tenant isolation is proven as the NON-SUPERUSER
`skylize_app` role, because a superuser bypasses RLS even with FORCE. Tests:

  - the app role is genuinely NOSUPERUSER / NOBYPASSRLS (the fix actually landed);
  - RLS tenant isolation: bound to org A, the app role cannot see org B's rows;
  - WITH CHECK: the app role cannot INSERT a row for another tenant;
  - append-only audit_log: UPDATE/DELETE are rejected (trigger + least-privilege);
  - rehydrate carve-out (Task 2): the read-only flag sees all tenants, no write;
  - migration correctness: tables, FORCE RLS, trigger exist.

Each test uses unique org_ids and cleans up, so it is safe against the shared
`public` schema. Skipped unless SKYLIZE_TEST_DB_URL (+ APP_DB_URL) are set.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from .conftest import requires_app_role, requires_pg

pytestmark = pytest.mark.integration


def _orgs() -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    return f"org_a_{suffix}", f"org_b_{suffix}"


async def _seed_tenant(conn, org: str) -> None:
    await conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _bind(conn, org: str) -> None:
    await conn.execute("SELECT set_config('skylize.org_id', $1, false)", org)


async def _insert_audit(conn, org: str) -> uuid.UUID:
    event_id = uuid.uuid4()
    await conn.execute(
        """INSERT INTO audit_log (event_id, org_id, tenant_id, correlation_id,
           action_type, result, occurred_at)
           VALUES ($1,$2,$2,$3,$4,$5,$6)""",
        event_id, org, uuid.uuid4(), "orchestrator.run", "success",
        datetime.now(timezone.utc),
    )
    return event_id


# ---------------------------------------------------------------------------
# The fix actually landed: app role is subject to RLS
# ---------------------------------------------------------------------------

@requires_app_role
async def test_app_role_is_not_superuser_or_bypassrls(app_conn, admin_conn) -> None:
    """The runtime role MUST be NOSUPERUSER + NOBYPASSRLS, else RLS is moot."""
    role = await app_conn.fetchval("SELECT current_user")
    row = await admin_conn.fetchrow(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = $1", role
    )
    assert row["rolsuper"] is False, f"{role} is a superuser — it would bypass RLS"
    assert row["rolbypassrls"] is False, f"{role} has BYPASSRLS — it would bypass RLS"


# ---------------------------------------------------------------------------
# RLS isolation — proven as the app role
# ---------------------------------------------------------------------------

@requires_app_role
async def test_rls_blocks_cross_tenant_read(app_conn, admin_conn) -> None:
    org_a, org_b = _orgs()
    try:
        await _seed_tenant(admin_conn, org_a)
        await _seed_tenant(admin_conn, org_b)
        for org in (org_a, org_b):
            await _bind(app_conn, org)
            await _insert_audit(app_conn, org)

        # Bound to org_a, the app role sees ONLY org_a's rows.
        await _bind(app_conn, org_a)
        rows = await app_conn.fetch("SELECT org_id FROM audit_log")
        seen = {r["org_id"] for r in rows}
        assert seen == {org_a}, f"RLS leaked another tenant's rows: {seen}"
    finally:
        # Cleanup must delete audit rows (append-only blocks DELETE) — do it as
        # admin which can DROP via cascade on the tenants we created.
        await admin_conn.execute("DELETE FROM tenants WHERE org_id = ANY($1::text[])",
                                 [org_a, org_b])


@requires_app_role
async def test_with_check_blocks_cross_tenant_insert(app_conn, admin_conn) -> None:
    org_a, org_b = _orgs()
    try:
        await _seed_tenant(admin_conn, org_a)
        await _seed_tenant(admin_conn, org_b)
        await _bind(app_conn, org_a)
        import asyncpg

        # Bound to org_a, inserting an org_b row violates the WITH CHECK policy.
        with pytest.raises(asyncpg.PostgresError):
            await _insert_audit(app_conn, org_b)
    finally:
        await admin_conn.execute("DELETE FROM tenants WHERE org_id = ANY($1::text[])",
                                 [org_a, org_b])


@requires_app_role
async def test_append_only_trigger_blocks_update_and_delete(app_conn, admin_conn) -> None:
    org_a, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org_a)
        await _bind(app_conn, org_a)
        await _insert_audit(app_conn, org_a)
        import asyncpg

        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await app_conn.execute("UPDATE audit_log SET result='tampered' WHERE org_id=$1",
                                   org_a)
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await app_conn.execute("DELETE FROM audit_log WHERE org_id=$1", org_a)
    finally:
        await admin_conn.execute("DELETE FROM tenants WHERE org_id = $1", org_a)


@requires_app_role
async def test_rehydrate_flag_reads_all_tenants_but_cannot_write(app_conn, admin_conn) -> None:
    org_a, org_b = _orgs()
    try:
        await _seed_tenant(admin_conn, org_a)
        await _seed_tenant(admin_conn, org_b)
        for org in (org_a, org_b):
            await _bind(app_conn, org)
            await app_conn.execute(
                """INSERT INTO kill_switch_state
                   (scope_type, scope_id, org_id, engaged_at, engaged_by, reason)
                   VALUES ('tenant',$1,$1, now(), 'owner', 'incident')""",
                org,
            )

        # Rehydrate read carve-out: clear org binding, set flag → sees BOTH.
        await app_conn.execute("SELECT set_config('skylize.org_id', '', false)")
        await app_conn.execute("SELECT set_config('skylize.rehydrate', 'on', false)")
        rows = await app_conn.fetch(
            "SELECT org_id FROM kill_switch_state WHERE org_id = ANY($1::text[])",
            [org_a, org_b],
        )
        assert {r["org_id"] for r in rows} == {org_a, org_b}

        # WITH CHECK still forbids a cross-tenant WRITE even with the flag set.
        import asyncpg

        await app_conn.execute("SELECT set_config('skylize.org_id', $1, false)", org_a)
        with pytest.raises(asyncpg.PostgresError):
            await app_conn.execute(
                """INSERT INTO kill_switch_state (scope_type, scope_id, org_id)
                   VALUES ('tenant',$1,$1)""",
                org_b,
            )
    finally:
        await admin_conn.execute(
            "DELETE FROM kill_switch_state WHERE org_id = ANY($1::text[])", [org_a, org_b]
        )
        await admin_conn.execute("DELETE FROM tenants WHERE org_id = ANY($1::text[])",
                                 [org_a, org_b])


# ---------------------------------------------------------------------------
# Migration correctness (object existence) — disposable schema, admin role
# ---------------------------------------------------------------------------

@requires_pg
async def test_migration_created_core_objects(pg_schema: str) -> None:
    import asyncpg

    from .conftest import DB_URL

    conn = await asyncpg.connect(DB_URL)
    await conn.execute(f'SET search_path TO "{pg_schema}"')
    try:
        tables = {
            r["tablename"]
            for r in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname=$1", pg_schema
            )
        }
        assert {"governance_tokens", "audit_log", "kill_switch_state", "tenants"} <= tables

        forced = await conn.fetchval(
            "SELECT relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE c.relname='audit_log' AND n.nspname=$1",
            pg_schema,
        )
        assert forced is True

        trig = await conn.fetchval(
            "SELECT count(*) FROM pg_trigger t "
            "JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE t.tgname='audit_log_append_only' AND n.nspname=$1",
            pg_schema,
        )
        assert trig == 1
    finally:
        await conn.close()
