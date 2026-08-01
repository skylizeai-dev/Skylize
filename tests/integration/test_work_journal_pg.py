"""work_journal / journal_cursor integration tests — REAL Postgres, app role.

Covers what only a database can prove (migration 0019):
  * migration shape: FORCE RLS + tenant_isolation on both tables, the
    work_journal_append_only trigger IS present (unlike org_spend_ceiling,
    which deliberately has none), and grants match the append-only posture
    (work_journal: SELECT/INSERT only; journal_cursor: full DML);
  * RLS: one org cannot read another org's journal entries, proven as a role
    that is neither superuser nor the table owner;
  * append-only: a raw UPDATE or DELETE against work_journal is rejected by
    the trigger, regardless of who attempts it;
  * PostgresJournalRepository round-trip: append -> since -> get_cursor
    (None) -> advance_cursor -> get_cursor (populated) -> head_seq, and the
    cursor is monotonic (an older to_seq cannot move it backward).

Skipped unless SKYLIZE_TEST_DB_URL (+ APP_DB_URL) are set.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import asyncpg
import pytest

from skylize.app.principal.models import ActorKind, JournalEntry
from skylize.dal.work_journal import PostgresJournalRepository

from .conftest import DB_URL, requires_app_role, requires_pg

pytestmark = pytest.mark.integration


def _orgs() -> tuple[str, str]:
    s = uuid.uuid4().hex[:8]
    return f"journal_a_{s}", f"journal_b_{s}"


async def _seed_tenant(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _cleanup(admin_conn, orgs: list[str]) -> None:
    await admin_conn.execute("DELETE FROM journal_cursor WHERE org_id = ANY($1::text[])", orgs)
    await admin_conn.execute("TRUNCATE work_journal")  # append-only: DELETE is blocked
    await admin_conn.execute("DELETE FROM tenants WHERE org_id = ANY($1::text[])", orgs)


def _entry(*, org_id: str, principal_id: str, headline: str) -> JournalEntry:
    return JournalEntry(
        seq=0,
        org_id=org_id,
        principal_id=principal_id,
        actor_kind=ActorKind.AGENT_AUTONOMOUS,
        actor_id="test_agent",
        correlation_id=uuid.uuid4(),
        kind="test.happened",
        headline=headline,
        occurred_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Migration shape — disposable schema, admin role
# ---------------------------------------------------------------------------

@requires_pg
async def test_migration_forced_rls_append_only_trigger_and_grants(pg_schema: str) -> None:
    conn = await asyncpg.connect(DB_URL)
    await conn.execute(f'SET search_path TO "{pg_schema}"')
    try:
        for table in ("work_journal", "journal_cursor"):
            exists = await conn.fetchval(
                "SELECT count(*) FROM pg_tables WHERE schemaname=$1 AND tablename=$2",
                pg_schema, table,
            )
            assert exists == 1, f"{table} does not exist"

            flags = await conn.fetchrow(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE c.relname=$1 AND n.nspname=$2",
                table, pg_schema,
            )
            assert flags["relrowsecurity"] is True, f"{table} RLS not enabled"
            assert flags["relforcerowsecurity"] is True, f"{table} RLS not forced"

            pol = await conn.fetchval(
                "SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE c.relname=$1 AND n.nspname=$2 AND p.polname='tenant_isolation'",
                table, pg_schema,
            )
            assert pol == 1, f"{table} missing tenant_isolation policy"

        # work_journal IS append-only: exactly one non-internal trigger.
        trig = await conn.fetchval(
            "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE c.relname='work_journal' AND n.nspname=$1 AND NOT t.tgisinternal",
            pg_schema,
        )
        assert trig == 1

        # journal_cursor has NO such trigger — it is mutable config.
        cursor_trig = await conn.fetchval(
            "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE c.relname='journal_cursor' AND n.nspname=$1 AND NOT t.tgisinternal",
            pg_schema,
        )
        assert cursor_trig == 0

        # Grants match the append-only posture (migration 0019).
        wj_grants = {
            r["privilege_type"]
            for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE table_schema=$1 AND table_name='work_journal' AND grantee='skylize_app'",
                pg_schema,
            )
        }
        assert wj_grants == {"SELECT", "INSERT"}

        cursor_grants = {
            r["privilege_type"]
            for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE table_schema=$1 AND table_name='journal_cursor' AND grantee='skylize_app'",
                pg_schema,
            )
        }
        assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= cursor_grants
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Append-only — a raw UPDATE/DELETE against work_journal is always rejected
# ---------------------------------------------------------------------------

@requires_app_role
async def test_append_only_trigger_rejects_update_and_delete(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        repo = PostgresJournalRepository(app_db)
        seq = await repo.append(_entry(org_id=org, principal_id="p1", headline="immutable"))

        async with app_db.tenant_session(org) as conn:
            with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
                await conn.execute("UPDATE work_journal SET headline='changed' WHERE seq=$1", seq)
        async with app_db.tenant_session(org) as conn:
            with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
                await conn.execute("DELETE FROM work_journal WHERE seq=$1", seq)

        # The row is untouched — neither statement partially applied.
        entries = await repo.since(org_id=org, principal_id="p1", after_seq=0)
        assert len(entries) == 1
        assert entries[0].headline == "immutable"
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# RLS — cross-tenant isolation proven as a non-superuser, non-owner role
# ---------------------------------------------------------------------------

@requires_app_role
async def test_rls_blocks_cross_tenant_journal_read(app_db, app_conn, admin_conn) -> None:
    org_a, org_b = _orgs()
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        repo = PostgresJournalRepository(app_db)
        await repo.append(_entry(org_id=org_a, principal_id="p1", headline="org A's own thing"))
        await repo.append(_entry(org_id=org_b, principal_id="p1", headline="org B's own thing"))

        async with app_db.tenant_session(org_a) as conn:
            seen = {r["org_id"] for r in await conn.fetch("SELECT org_id FROM work_journal")}
        assert seen == {org_a}

        a_entries = await repo.since(org_id=org_a, principal_id="p1", after_seq=0)
        b_entries = await repo.since(org_id=org_b, principal_id="p1", after_seq=0)
        assert [e.headline for e in a_entries] == ["org A's own thing"]
        assert [e.headline for e in b_entries] == ["org B's own thing"]

        role = await app_conn.fetchval("SELECT current_user")
        rolrow = await admin_conn.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=$1", role
        )
        assert rolrow["rolsuper"] is False, f"{role} is a superuser — would bypass RLS"
        assert rolrow["rolbypassrls"] is False, f"{role} has BYPASSRLS — would bypass RLS"
        owner = await admin_conn.fetchval(
            "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE relname='work_journal'"
        )
        assert owner != role, f"{role} owns the table — owner bypasses RLS unless FORCE"
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


# ---------------------------------------------------------------------------
# Repository round-trip: append -> since -> cursor -> head_seq
# ---------------------------------------------------------------------------

@requires_app_role
async def test_repository_round_trip_and_monotonic_cursor(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        repo = PostgresJournalRepository(app_db)

        assert await repo.get_cursor(org_id=org, principal_id="p1") is None
        assert await repo.head_seq(org_id=org, principal_id="p1") == 0

        seq1 = await repo.append(_entry(org_id=org, principal_id="p1", headline="first"))
        seq2 = await repo.append(_entry(org_id=org, principal_id="p1", headline="second"))
        assert seq2 == seq1 + 1
        assert await repo.head_seq(org_id=org, principal_id="p1") == seq2

        since_zero = await repo.since(org_id=org, principal_id="p1", after_seq=0)
        assert [e.headline for e in since_zero] == ["first", "second"]
        since_first = await repo.since(org_id=org, principal_id="p1", after_seq=seq1)
        assert [e.headline for e in since_first] == ["second"]

        now = datetime.now(timezone.utc)
        await repo.advance_cursor(org_id=org, principal_id="p1", to_seq=seq1, at=now)
        cursor = await repo.get_cursor(org_id=org, principal_id="p1")
        assert cursor is not None
        assert cursor.last_seen_seq == seq1

        # Monotonic: an older to_seq can never move the cursor backward.
        await repo.advance_cursor(org_id=org, principal_id="p1", to_seq=0, at=now)
        cursor = await repo.get_cursor(org_id=org, principal_id="p1")
        assert cursor.last_seen_seq == seq1

        await repo.advance_cursor(org_id=org, principal_id="p1", to_seq=seq2, at=now)
        cursor = await repo.get_cursor(org_id=org, principal_id="p1")
        assert cursor.last_seen_seq == seq2
    finally:
        await _cleanup(admin_conn, [org])
