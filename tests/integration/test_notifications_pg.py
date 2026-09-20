"""notifications integration tests — REAL Postgres, proven as the app role.

Covers what only a database can prove for migration 0034 and
``NotificationsDAL``:
  * migration shape: FORCE RLS, tenant_isolation policy, the two-value kind
    CHECK, the three-value severity CHECK, SELECT/INSERT/UPDATE grants and NO
    DELETE grant;
  * record/list/mark_read round-trip correctly;
  * list respects the unread filter, the keyset cursor, and newest-first order;
  * mark_read is idempotent — a second call does not move ``read_at``;
  * RLS: one org can neither READ nor WRITE nor ACKNOWLEDGE another org's
    notifications, proven as a role that is neither superuser nor the table
    owner;
  * ``record`` is best-effort: an unknown kind/severity is refused before
    reaching the database and never raises.

Skipped unless SKYLIZE_TEST_DB_URL (+ SKYLIZE_TEST_APP_DB_URL) are set. The app
role must be the non-superuser, non-owner ``skylize_app`` role or the isolation
tests prove nothing; that is asserted in the RLS test itself, not assumed.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from skylize.dal.connection import Database
from skylize.dal.notifications import NotificationsDAL

from .conftest import APP_DB_URL, requires_app_role, requires_pg

pytestmark = pytest.mark.integration


def _orgs() -> tuple[str, str]:
    s = uuid.uuid4().hex[:8]
    return f"notif_a_{s}", f"notif_b_{s}"


async def _seed_tenant(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _cleanup(admin_conn, orgs: list[str]) -> None:
    await admin_conn.execute(
        "DELETE FROM notifications WHERE org_id = ANY($1::text[])", orgs
    )
    await admin_conn.execute("DELETE FROM tenants WHERE org_id = ANY($1::text[])", orgs)


@pytest_asyncio.fixture()
async def app_db(migrated_public: None):
    """A ``Database`` pool connected as the NON-SUPERUSER app role (RLS-subject)."""
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")
    db = Database(APP_DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Migration shape
# ---------------------------------------------------------------------------


@requires_pg
@pytest.mark.asyncio
async def test_migration_shape(admin_conn) -> None:
    schema = await admin_conn.fetchval("SELECT current_schema()")

    row = await admin_conn.fetchrow(
        "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname='notifications' AND n.nspname=$1",
        schema,
    )
    assert row["relrowsecurity"] is True, "RLS is not enabled on notifications"
    assert row["relforcerowsecurity"] is True, (
        "RLS is not FORCED — the table owner would bypass the policy"
    )

    policy = await admin_conn.fetchval(
        "SELECT p.polname FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname='notifications' AND n.nspname=$1 "
        "AND p.polname='tenant_isolation'",
        schema,
    )
    assert policy == "tenant_isolation"

    checks = [
        r["definition"]
        for r in await admin_conn.fetch(
            "SELECT pg_get_constraintdef(con.oid) AS definition FROM pg_constraint con "
            "JOIN pg_class c ON c.oid = con.conrelid "
            "WHERE c.relname='notifications' AND con.contype='c'"
        )
    ]
    combined = " ".join(checks)
    for kind in ("hitl.approval_requested", "governance.action_denied"):
        assert kind in combined, f"{kind} missing from a CHECK constraint"
    for sev in ("info", "warning", "critical"):
        assert sev in combined, f"{sev} missing from a CHECK constraint"

    grants = {
        r["privilege_type"]
        for r in await admin_conn.fetch(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name='notifications' AND grantee='skylize_app'"
        )
    }
    assert {"SELECT", "INSERT", "UPDATE"} <= grants
    assert "DELETE" not in grants, (
        "notifications must not be deletable by the app role"
    )


@requires_pg
@pytest.mark.asyncio
async def test_check_constraint_rejects_an_unknown_kind(admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        with pytest.raises(Exception) as excinfo:
            await admin_conn.execute(
                "INSERT INTO notifications "
                "(notification_id, org_id, kind, severity, title, body) "
                "VALUES ($1,$2,$3,$4,$5,$6)",
                uuid.uuid4(), org, "made.up.kind", "warning", "t", "b",
            )
        assert "notifications" in str(excinfo.value)
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# record / list / mark_read
# ---------------------------------------------------------------------------


@requires_app_role
@pytest.mark.asyncio
async def test_record_then_list_round_trips(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        dal = NotificationsDAL(app_db)
        correlation_id = uuid.uuid4()
        new_id = await dal.record(
            org_id=org,
            kind="hitl.approval_requested",
            severity="warning",
            title="Approval needed",
            body="agent x is waiting",
            correlation_id=correlation_id,
        )
        assert new_id is not None

        rows = await dal.list_for_org(org)
        assert len(rows) == 1
        assert rows[0].notification_id == new_id
        assert rows[0].kind == "hitl.approval_requested"
        assert rows[0].correlation_id == correlation_id
        assert rows[0].read_at is None
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_list_is_newest_first_and_respects_unread_filter(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        dal = NotificationsDAL(app_db)
        first = await dal.record(
            org_id=org, kind="hitl.approval_requested", severity="warning",
            title="first", body="b",
        )
        second = await dal.record(
            org_id=org, kind="governance.action_denied", severity="critical",
            title="second", body="b",
        )
        assert first is not None and second is not None

        rows = await dal.list_for_org(org)
        assert [r.notification_id for r in rows] == [second, first]

        await dal.mark_read(org_id=org, notification_id=second)
        unread = await dal.list_for_org(org, unread_only=True)
        assert [r.notification_id for r in unread] == [first]
        assert await dal.unread_count(org) == 1
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_list_keyset_cursor_pages_older_rows(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        dal = NotificationsDAL(app_db)
        for i in range(3):
            await dal.record(
                org_id=org, kind="hitl.approval_requested", severity="warning",
                title=f"n{i}", body="b",
            )
        first_page = await dal.list_for_org(org, limit=2)
        assert len(first_page) == 2
        second_page = await dal.list_for_org(
            org, limit=2, before=first_page[-1].created_at
        )
        # No overlap between the two pages.
        first_ids = {r.notification_id for r in first_page}
        second_ids = {r.notification_id for r in second_page}
        assert first_ids.isdisjoint(second_ids)
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_mark_read_is_idempotent(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        dal = NotificationsDAL(app_db)
        nid = await dal.record(
            org_id=org, kind="hitl.approval_requested", severity="warning",
            title="t", body="b",
        )
        assert nid is not None

        first = await dal.mark_read(org_id=org, notification_id=nid)
        assert first is not None and first.read_at is not None

        second = await dal.mark_read(org_id=org, notification_id=nid)
        assert second is not None
        assert second.read_at == first.read_at, "a second ack must not move read_at"
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_mark_read_returns_none_for_an_unknown_id(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        dal = NotificationsDAL(app_db)
        result = await dal.mark_read(org_id=org, notification_id=uuid.uuid4())
        assert result is None
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_record_rejects_an_unknown_kind_without_raising(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        dal = NotificationsDAL(app_db)
        result = await dal.record(
            org_id=org, kind="made.up", severity="warning", title="t", body="b"  # type: ignore[arg-type]
        )
        assert result is None
        assert await dal.list_for_org(org) == []
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# RLS — the isolation that only a real database proves
# ---------------------------------------------------------------------------


@requires_app_role
@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_notification_read(
    app_db, app_conn, admin_conn
) -> None:
    org_a, org_b = _orgs()
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        dal = NotificationsDAL(app_db)
        await dal.record(
            org_id=org_a, kind="hitl.approval_requested", severity="warning",
            title="a", body="b",
        )
        await dal.record(
            org_id=org_b, kind="governance.action_denied", severity="critical",
            title="b", body="b",
        )

        # Bound to org_a, a raw SELECT sees ONLY org_a's row.
        async with app_db.tenant_session(org_a) as conn:
            seen = {
                r["org_id"] for r in await conn.fetch("SELECT org_id FROM notifications")
            }
        assert seen == {org_a}

        assert len(await dal.list_for_org(org_a)) == 1
        assert len(await dal.list_for_org(org_b)) == 1

        # Prove the RLS-subject role is NEITHER superuser NOR the table owner.
        role = await app_conn.fetchval("SELECT current_user")
        rolrow = await admin_conn.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=$1", role
        )
        assert rolrow["rolsuper"] is False, f"{role} is a superuser — would bypass RLS"
        assert rolrow["rolbypassrls"] is False, (
            f"{role} has BYPASSRLS — would bypass RLS"
        )
        owner = await admin_conn.fetchval(
            "SELECT pg_get_userbyid(relowner) FROM pg_class "
            "WHERE relname='notifications'"
        )
        assert owner != role, f"{role} owns the table — owner bypasses RLS unless FORCE"
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


@requires_app_role
@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_notification_write(app_db, admin_conn) -> None:
    """A session bound to org_a cannot insert a row for org_b."""
    org_a, org_b = _orgs()
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        async with app_db.tenant_session(org_a) as conn:
            with pytest.raises(Exception):
                await conn.execute(
                    "INSERT INTO notifications "
                    "(notification_id, org_id, kind, severity, title, body) "
                    "VALUES ($1,$2,$3,$4,$5,$6)",
                    uuid.uuid4(), org_b, "hitl.approval_requested", "warning", "t", "b",
                )
        dal = NotificationsDAL(app_db)
        assert await dal.list_for_org(org_b) == []
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


@requires_app_role
@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_mark_read(app_db, admin_conn) -> None:
    """A session bound to org_a cannot acknowledge org_b's notification, even
    holding its exact id — the WITH CHECK / USING half of tenant_isolation makes
    the row simply not match, which mark_read reports as 'not found'."""
    org_a, org_b = _orgs()
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        dal = NotificationsDAL(app_db)
        nid = await dal.record(
            org_id=org_b, kind="hitl.approval_requested", severity="warning",
            title="t", body="b",
        )
        assert nid is not None

        # A DAL bound to org_a's session cannot see or ack org_b's row.
        result = await dal.mark_read(org_id=org_a, notification_id=nid)
        assert result is None

        # org_b's row is untouched.
        still_unread = await dal.list_for_org(org_b, unread_only=True)
        assert len(still_unread) == 1
    finally:
        await _cleanup(admin_conn, [org_a, org_b])
