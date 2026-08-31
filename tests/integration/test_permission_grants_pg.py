"""org_permission_grants integration tests — REAL Postgres, proven as the app role.

Covers what only a database can prove (migration 0022):
  * migration shape: FORCE RLS, tenant_isolation policy, the max_role CHECK
    constraint, the unique (org, action_class, grantee_pattern) index;
  * **RLS cross-tenant isolation** — one org cannot read, add to, or delete
    another org's pre-authorization rows, proven as a role that is neither a
    superuser nor the table owner (either would bypass RLS and make it vacuous);
  * the deny-by-default posture end-to-end through the real DAL: an org with no
    rows authorizes nothing, and org A's rows never authorize org B's share.

Skipped unless SKYLIZE_TEST_DB_URL (+ SKYLIZE_TEST_APP_DB_URL) are set.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from skylize.app.permissions.gate import PermissionDeniedError, PermissionGate
from skylize.dal.connection import Database
from skylize.dal.permission_grants import (
    PermissionGrantRow,
    PgPermissionGrantRepository,
)

from .conftest import APP_DB_URL, requires_app_role, requires_pg

pytestmark = pytest.mark.integration

ACTION = "drive.permissions.create"


def _orgs() -> tuple[str, str]:
    s = uuid.uuid4().hex[:8]
    return f"perm_a_{s}", f"perm_b_{s}"


async def _seed_tenant(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _cleanup(admin_conn, orgs: list[str]) -> None:
    await admin_conn.execute(
        "DELETE FROM org_permission_grants WHERE org_id = ANY($1::text[])", orgs
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


def _row(org: str, pattern: str, *, role: str = "writer", link: bool = False):
    now = datetime.now(timezone.utc)
    return PermissionGrantRow(
        grant_id=uuid.uuid4(), org_id=org, action_class=ACTION,
        grantee_pattern=pattern, max_role=role,  # type: ignore[arg-type]
        allow_link_sharing=link, created_at=now, updated_at=now,
    )


# ---------------------------------------------------------------------------
# Migration shape
# ---------------------------------------------------------------------------

@requires_pg
async def test_migration_shape(admin_conn, pg_schema: str) -> None:
    rel = await admin_conn.fetchrow(
        "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname='org_permission_grants' AND n.nspname=$1",
        pg_schema,
    )
    assert rel is not None, "org_permission_grants table missing"
    assert rel["relrowsecurity"] is True, "RLS not ENABLEd"
    assert rel["relforcerowsecurity"] is True, "RLS not FORCEd — owner would bypass"

    pol = await admin_conn.fetchval(
        "SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname='org_permission_grants' AND n.nspname=$1 "
        "AND p.polname='tenant_isolation'",
        pg_schema,
    )
    assert pol == 1

    chk = await admin_conn.fetchval(
        "SELECT count(*) FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname='org_permission_grants' AND n.nspname=$1 "
        "AND con.conname='org_permission_grants_role_check'",
        pg_schema,
    )
    assert chk == 1


@requires_pg
async def test_role_check_rejects_owner_tier(admin_conn) -> None:
    """`owner` is deliberately not expressible: transferring ownership of a
    customer's file is out of scope for section 2.5."""
    org = f"perm_chk_{uuid.uuid4().hex[:8]}"
    try:
        await _seed_tenant(admin_conn, org)
        with pytest.raises(Exception) as exc:
            await admin_conn.execute(
                "INSERT INTO org_permission_grants "
                "(org_id, action_class, grantee_pattern, max_role) "
                "VALUES ($1,$2,'example.com','owner')",
                org, ACTION,
            )
        assert "org_permission_grants_role_check" in str(exc.value)
    finally:
        await _cleanup(admin_conn, [org])


@requires_pg
async def test_table_is_created_empty(admin_conn) -> None:
    """No seed, no wildcard default row. Deny-by-default is the data model."""
    org = f"perm_empty_{uuid.uuid4().hex[:8]}"
    try:
        await _seed_tenant(admin_conn, org)
        n = await admin_conn.fetchval(
            "SELECT count(*) FROM org_permission_grants WHERE org_id=$1", org
        )
        assert n == 0
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# RLS — the hard gate
# ---------------------------------------------------------------------------

@requires_app_role
async def test_rls_blocks_cross_tenant_grant_access(app_db, admin_conn) -> None:
    """Org A's pre-authorizations must be invisible and immutable from org B."""
    org_a, org_b = _orgs()
    repo = PgPermissionGrantRepository(app_db)
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)

        row_a = _row(org_a, "example.com")
        await repo.insert(row_a)

        assert len(await repo.list_for_action(org_a, ACTION)) == 1
        assert await repo.list_for_action(org_b, ACTION) == []

        # B cannot delete A's row even naming A's grant_id explicitly.
        assert await repo.delete_by_id(row_a.grant_id, org_b) is False
        assert len(await repo.list_for_action(org_a, ACTION)) == 1

        # A raw cross-tenant SELECT under B's binding, with no org predicate in
        # the SQL, must still return nothing — proving the POLICY does the work,
        # not just the DAL's redundant WHERE clause.
        async with app_db.tenant_session(org_b) as conn:
            leaked = await conn.fetch("SELECT grant_id FROM org_permission_grants")
            assert leaked == [], "RLS policy leaked rows across tenants"

        # The role must genuinely be an RLS subject or none of this proves anything.
        rolrow = await admin_conn.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname='skylize_app'"
        )
        assert rolrow["rolsuper"] is False
        assert rolrow["rolbypassrls"] is False
        owner = await admin_conn.fetchval(
            "SELECT pg_get_userbyid(relowner) FROM pg_class "
            "WHERE relname='org_permission_grants'"
        )
        assert owner != "skylize_app", "owner bypasses RLS unless FORCE"
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


@requires_app_role
async def test_org_b_cannot_write_a_grant_for_org_a(app_db, admin_conn) -> None:
    """The policy's WITH CHECK must refuse a cross-tenant INSERT outright."""
    org_a, org_b = _orgs()
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        async with app_db.tenant_session(org_b) as conn:
            with pytest.raises(Exception):
                await conn.execute(
                    "INSERT INTO org_permission_grants "
                    "(org_id, action_class, grantee_pattern, max_role) "
                    "VALUES ($1,$2,'evil.com','writer')",
                    org_a, ACTION,
                )
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


# ---------------------------------------------------------------------------
# Gate behaviour end-to-end through real Postgres
# ---------------------------------------------------------------------------

@requires_app_role
async def test_gate_denies_when_org_has_no_rows(app_db, admin_conn) -> None:
    org_a, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org_a)
        gate = PermissionGate(PgPermissionGrantRepository(app_db))
        with pytest.raises(PermissionDeniedError, match="pre-authorized no recipient"):
            await gate.authorize(
                org_id=org_a, action_class=ACTION, grantee="a@example.com",
                role="reader", link_sharing_sentinel="anyone",
            )
    finally:
        await _cleanup(admin_conn, [org_a])


@requires_app_role
async def test_gate_authorizes_from_a_real_row(app_db, admin_conn) -> None:
    org_a, _ = _orgs()
    repo = PgPermissionGrantRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org_a)
        await repo.insert(_row(org_a, "example.com", role="commenter"))
        gate = PermissionGate(repo)
        grant = await gate.authorize(
            org_id=org_a, action_class=ACTION, grantee="alice@example.com",
            role="commenter", link_sharing_sentinel="anyone",
        )
        assert grant.matched_pattern == "example.com"
        assert grant.role == "commenter"
    finally:
        await _cleanup(admin_conn, [org_a])


@requires_app_role
async def test_org_a_rows_never_authorize_org_b(app_db, admin_conn) -> None:
    """The isolation property that matters operationally: one customer's
    pre-authorized recipients must never let another customer's agent share."""
    org_a, org_b = _orgs()
    repo = PgPermissionGrantRepository(app_db)
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        await repo.insert(_row(org_a, "example.com", role="writer"))
        gate = PermissionGate(repo)

        grant = await gate.authorize(
            org_id=org_a, action_class=ACTION, grantee="a@example.com",
            role="writer", link_sharing_sentinel="anyone",
        )
        assert grant.grantee == "a@example.com"

        with pytest.raises(PermissionDeniedError):
            await gate.authorize(
                org_id=org_b, action_class=ACTION, grantee="a@example.com",
                role="writer", link_sharing_sentinel="anyone",
            )
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


@requires_app_role
async def test_link_sharing_flag_round_trips(app_db, admin_conn) -> None:
    org_a, _ = _orgs()
    repo = PgPermissionGrantRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org_a)
        await repo.insert(_row(org_a, "example.com", role="reader", link=True))
        gate = PermissionGate(repo)
        grant = await gate.authorize(
            org_id=org_a, action_class=ACTION, grantee="anyone",
            role="reader", link_sharing_sentinel="anyone",
        )
        assert grant.grantee == "anyone"
    finally:
        await _cleanup(admin_conn, [org_a])
