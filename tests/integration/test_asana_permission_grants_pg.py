"""Asana's action classes against REAL Postgres, proven as the app role.

The Asana connector adds NO table and NO migration — it reuses
`org_permission_grants` (0022) and `oauth_credentials` (0021), both already covered
by their own suites. What is NOT covered by those suites, and is what this file
exists for, is that Asana's *particular* reuse is safe:

  * Asana's two action classes are tenant-isolated by the same RLS policy, proven
    as a role that is neither superuser nor table owner (either would bypass RLS
    and make the assertion vacuous);
  * the two Asana classes do not cross-authorize each other at the DAL level —
    project membership must never pre-authorize organization-wide invitation
    (2.6 Q2.6d), asserted against real rows rather than against in-memory stubs;
  * the `max_role` CHECK constraint is STILL the three-value Drive vocabulary.
    2.6 Q2.6e maps Asana's role-less membership to a fixed `writer` in application
    code precisely so this constraint need not be relaxed; a test that the
    constraint still rejects everything else is what keeps that decision honest.

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
from skylize.tools.builtin.asana_tools import (
    ASANA_ADD_PROJECT_MEMBER_ACTION_CLASS,
    ASANA_ADD_WORKSPACE_USER_ACTION_CLASS,
)

from .conftest import APP_DB_URL, requires_app_role, requires_pg

pytestmark = pytest.mark.integration

PROJECT_ACTION = ASANA_ADD_PROJECT_MEMBER_ACTION_CLASS
WORKSPACE_ACTION = ASANA_ADD_WORKSPACE_USER_ACTION_CLASS


def _orgs() -> tuple[str, str]:
    s = uuid.uuid4().hex[:8]
    return f"asana_a_{s}", f"asana_b_{s}"


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


def _row(org: str, action_class: str, pattern: str, *, role: str = "writer"):
    now = datetime.now(timezone.utc)
    return PermissionGrantRow(
        grant_id=uuid.uuid4(), org_id=org, action_class=action_class,
        grantee_pattern=pattern, max_role=role,  # type: ignore[arg-type]
        allow_link_sharing=False, created_at=now, updated_at=now,
    )


# ---------------------------------------------------------------------------
# The CHECK constraint was NOT relaxed (2.6 Q2.6e)
# ---------------------------------------------------------------------------

@requires_pg
async def test_max_role_check_still_rejects_a_role_less_escape_hatch(
    admin_conn,
) -> None:
    """Asana has no role axis, and the schema must NOT have been widened for it.

    A nullable or free-text `max_role` would be an escape hatch any future
    provider could use to skip the rank comparison entirely. 2.6 Q2.6e maps Asana
    to a fixed `writer` in application code instead; this asserts the schema side
    of that bargain was actually kept.
    """
    org = f"asana_chk_{uuid.uuid4().hex[:8]}"
    try:
        await _seed_tenant(admin_conn, org)
        for bad_role in ("member", "collaborator", "none", "", "owner", "admin"):
            with pytest.raises(Exception):
                await admin_conn.execute(
                    "INSERT INTO org_permission_grants "
                    "(org_id, action_class, grantee_pattern, max_role) "
                    "VALUES ($1,$2,$3,$4)",
                    org, PROJECT_ACTION, "example.com", bad_role,
                )
        # And the value Asana actually maps to is accepted.
        await admin_conn.execute(
            "INSERT INTO org_permission_grants "
            "(org_id, action_class, grantee_pattern, max_role) VALUES ($1,$2,$3,$4)",
            org, PROJECT_ACTION, "example.com", "writer",
        )
    finally:
        await _cleanup(admin_conn, [org])


@requires_pg
async def test_max_role_cannot_be_null(admin_conn) -> None:
    org = f"asana_null_{uuid.uuid4().hex[:8]}"
    try:
        await _seed_tenant(admin_conn, org)
        with pytest.raises(Exception):
            await admin_conn.execute(
                "INSERT INTO org_permission_grants "
                "(org_id, action_class, grantee_pattern, max_role) "
                "VALUES ($1,$2,$3,NULL)",
                org, PROJECT_ACTION, "example.com",
            )
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# RLS isolation for Asana's action classes
# ---------------------------------------------------------------------------

@requires_app_role
async def test_rls_isolates_asana_rows_across_tenants(app_db, admin_conn) -> None:
    """Org B cannot see org A's Asana pre-authorizations."""
    org_a, org_b = _orgs()
    repo = PgPermissionGrantRepository(app_db)
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        await repo.insert(_row(org_a, PROJECT_ACTION, "example.com"))

        assert len(await repo.list_for_action(org_a, PROJECT_ACTION)) == 1
        assert await repo.list_for_action(org_b, PROJECT_ACTION) == [], (
            "org B read org A's Asana pre-authorization rows — RLS is not isolating"
        )
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


@requires_app_role
async def test_org_a_asana_rows_never_authorize_org_b(app_db, admin_conn) -> None:
    """One customer's authorized recipients must never let another's agent grant."""
    org_a, org_b = _orgs()
    repo = PgPermissionGrantRepository(app_db)
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        await repo.insert(_row(org_a, PROJECT_ACTION, "example.com"))
        gate = PermissionGate(repo)

        grant = await gate.authorize(
            org_id=org_a, action_class=PROJECT_ACTION, grantee="alice@example.com",
            role="writer", link_sharing_sentinel="anyone",
        )
        assert grant.grantee == "alice@example.com"

        with pytest.raises(PermissionDeniedError):
            await gate.authorize(
                org_id=org_b, action_class=PROJECT_ACTION,
                grantee="alice@example.com", role="writer",
                link_sharing_sentinel="anyone",
            )
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


# ---------------------------------------------------------------------------
# The two Asana classes do not cross-authorize (2.6 Q2.6d), against real rows
# ---------------------------------------------------------------------------

@requires_app_role
async def test_project_rows_do_not_authorize_workspace_invitation(
    app_db, admin_conn
) -> None:
    """The separation that makes two action classes worth having.

    An org that pre-authorized alice for project membership has NOT authorized
    inviting alice into the whole organization — a materially wider grant.
    """
    org_a, _ = _orgs()
    repo = PgPermissionGrantRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org_a)
        await repo.insert(_row(org_a, PROJECT_ACTION, "alice@example.com"))
        gate = PermissionGate(repo)

        # Authorized for the narrow verb...
        await gate.authorize(
            org_id=org_a, action_class=PROJECT_ACTION, grantee="alice@example.com",
            role="writer", link_sharing_sentinel="anyone",
        )
        # ...and denied for the wide one.
        with pytest.raises(PermissionDeniedError, match="pre-authorized no recipient"):
            await gate.authorize(
                org_id=org_a, action_class=WORKSPACE_ACTION,
                grantee="alice@example.com", role="writer",
                link_sharing_sentinel="anyone",
            )
    finally:
        await _cleanup(admin_conn, [org_a])


@requires_app_role
async def test_asana_rows_do_not_authorize_drive_sharing(app_db, admin_conn) -> None:
    """Cross-CONNECTOR separation, not just cross-verb.

    Asana pre-authorization must not leak into Drive's action class. Both live in
    one table keyed by `action_class`, so this is worth proving rather than assuming.
    """
    org_a, _ = _orgs()
    repo = PgPermissionGrantRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org_a)
        await repo.insert(_row(org_a, PROJECT_ACTION, "example.com"))
        gate = PermissionGate(repo)

        with pytest.raises(PermissionDeniedError, match="pre-authorized no recipient"):
            await gate.authorize(
                org_id=org_a, action_class="drive.permissions.create",
                grantee="alice@example.com", role="writer",
                link_sharing_sentinel="anyone",
            )
    finally:
        await _cleanup(admin_conn, [org_a])


@requires_app_role
async def test_reader_capped_row_denies_every_asana_membership(
    app_db, admin_conn
) -> None:
    """2.6 Q2.6e's documented consequence, against a real row.

    Every Asana membership request arrives as `writer`, so an org row capped at
    `reader` matches the recipient and still denies. Fail-closed by design.
    """
    org_a, _ = _orgs()
    repo = PgPermissionGrantRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org_a)
        await repo.insert(_row(org_a, PROJECT_ACTION, "example.com", role="reader"))
        gate = PermissionGate(repo)

        with pytest.raises(PermissionDeniedError, match="exceeds the maximum"):
            await gate.authorize(
                org_id=org_a, action_class=PROJECT_ACTION,
                grantee="alice@example.com", role="writer",
                link_sharing_sentinel="anyone",
            )
    finally:
        await _cleanup(admin_conn, [org_a])
