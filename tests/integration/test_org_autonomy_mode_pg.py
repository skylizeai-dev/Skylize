"""org_autonomy_mode integration tests — REAL Postgres, proven as the app role.

Covers what only a database can prove for migration 0028 and
``OrgAutonomyModeDAL`` (owner rulings 6 and 7):
  * migration shape: FORCE RLS, tenant_isolation policy, the five-value CHECK,
    SELECT/INSERT/UPDATE grants and NO DELETE grant;
  * fail-closed: a missing row reads as ``observe``, with no exceptions;
  * an explicit row reads back as exactly that mode, including an explicit
    ``observe`` that must stay distinguishable from "never set";
  * effective dating: a later row supersedes an earlier one; a future-dated row
    does not take effect early;
  * RLS: one org can neither READ nor WRITE another org's posture, proven as a
    role that is neither superuser nor the table owner;
  * every set_mode writes a governance audit record.

Skipped unless SKYLIZE_TEST_DB_URL (+ SKYLIZE_TEST_APP_DB_URL) are set. The app
role must be the non-superuser, non-owner ``skylize_app`` role or the isolation
tests prove nothing; that is asserted in the RLS test itself, not assumed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from skylize.app.audit.service import AuditService
from skylize.dal.connection import Database
from skylize.dal.memory import InMemoryAuditRepository
from skylize.dal.org_autonomy_mode import OrgAutonomyModeDAL
from skylize.events.memory_bus import InMemoryEventBus

from .conftest import APP_DB_URL, requires_app_role, requires_pg

pytestmark = pytest.mark.integration


def _orgs() -> tuple[str, str]:
    s = uuid.uuid4().hex[:8]
    return f"auto_a_{s}", f"auto_b_{s}"


def _audit() -> tuple[AuditService, InMemoryAuditRepository]:
    repo = InMemoryAuditRepository()
    return AuditService(InMemoryEventBus(), repo), repo


async def _seed_tenant(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _cleanup(admin_conn, orgs: list[str]) -> None:
    await admin_conn.execute(
        "DELETE FROM org_autonomy_mode WHERE org_id = ANY($1::text[])", orgs
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
        "WHERE c.relname='org_autonomy_mode' AND n.nspname=$1",
        schema,
    )
    assert row["relrowsecurity"] is True, "RLS is not enabled on org_autonomy_mode"
    assert row["relforcerowsecurity"] is True, (
        "RLS is not FORCED — the table owner would bypass the policy"
    )

    policy = await admin_conn.fetchval(
        "SELECT p.polname FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname='org_autonomy_mode' AND n.nspname=$1 "
        "AND p.polname='tenant_isolation'",
        schema,
    )
    assert policy == "tenant_isolation"

    # The CHECK must name exactly the five modes; a sixth value cannot enter
    # through SQL even if some caller skips the DAL's validation.
    check = await admin_conn.fetchval(
        "SELECT pg_get_constraintdef(con.oid) FROM pg_constraint con "
        "JOIN pg_class c ON c.oid = con.conrelid "
        "WHERE c.relname='org_autonomy_mode' AND con.contype='c'"
    )
    for mode in (
        "observe",
        "propose",
        "act_within_budget",
        "act_and_reallocate",
        "act_governed",
    ):
        assert mode in check, f"{mode} missing from the CHECK constraint"

    grants = {
        r["privilege_type"]
        for r in await admin_conn.fetch(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name='org_autonomy_mode' AND grantee='skylize_app'"
        )
    }
    assert {"SELECT", "INSERT", "UPDATE"} <= grants
    assert "DELETE" not in grants, (
        "posture history must not be deletable by the app role"
    )


@requires_pg
@pytest.mark.asyncio
async def test_check_constraint_rejects_an_unknown_mode(admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        with pytest.raises(Exception) as excinfo:
            await admin_conn.execute(
                "INSERT INTO org_autonomy_mode (org_id, autonomy_mode) VALUES ($1,$2)",
                org, "full_send",
            )
        assert "org_autonomy_mode" in str(excinfo.value)
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# Fail closed (ruling 7) and explicit reads
# ---------------------------------------------------------------------------


@requires_app_role
@pytest.mark.asyncio
async def test_missing_row_reads_as_observe(app_db, admin_conn) -> None:
    """An org nobody has configured gets `observe`. No exceptions (ruling 7)."""
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        dal = OrgAutonomyModeDAL(app_db)
        assert await dal.read_mode(org) == "observe"
        # ...and the display read still reports it as UNSET, so the console can
        # tell the owner they have never made this choice.
        assert await dal.read_configured_mode(org) is None
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ["observe", "propose", "act_within_budget", "act_and_reallocate", "act_governed"],
)
async def test_explicit_row_reads_back_exactly(app_db, admin_conn, mode: str) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        audit, _repo = _audit()
        dal = OrgAutonomyModeDAL(app_db)
        await dal.set_mode(
            org_id=org, autonomy_mode=mode, audit=audit, correlation_id=uuid.uuid4()
        )
        assert await dal.read_mode(org) == mode
        # An explicitly chosen `observe` is NOT the same fact as an unset org.
        assert await dal.read_configured_mode(org) == mode
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_later_row_supersedes_and_future_row_does_not_apply_early(
    app_db, admin_conn
) -> None:
    org, _ = _orgs()
    now = datetime.now(timezone.utc)
    try:
        await _seed_tenant(admin_conn, org)
        audit, _repo = _audit()
        dal = OrgAutonomyModeDAL(app_db)
        await dal.set_mode(
            org_id=org, autonomy_mode="propose", audit=audit,
            correlation_id=uuid.uuid4(), effective_from=now - timedelta(days=2),
        )
        await dal.set_mode(
            org_id=org, autonomy_mode="act_within_budget", audit=audit,
            correlation_id=uuid.uuid4(), effective_from=now - timedelta(days=1),
        )
        await dal.set_mode(
            org_id=org, autonomy_mode="act_governed", audit=audit,
            correlation_id=uuid.uuid4(), effective_from=now + timedelta(days=1),
        )

        # In force now: the most recent row at or before now.
        assert await dal.read_mode(org) == "act_within_budget"
        # Before any row existed the org still fails closed.
        assert await dal.read_mode(org, now - timedelta(days=3)) == "observe"
        # The older row is still what was in force back then — history is kept.
        assert await dal.read_mode(org, now - timedelta(days=1, hours=12)) == "propose"
        # The future-dated row does not take effect early.
        assert await dal.read_mode(org, now + timedelta(days=2)) == "act_governed"
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_set_mode_rejects_an_unknown_mode(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        audit, _repo = _audit()
        dal = OrgAutonomyModeDAL(app_db)
        with pytest.raises(ValueError, match="autonomy_mode must be one of"):
            await dal.set_mode(
                org_id=org,
                autonomy_mode="full_send",  # type: ignore[arg-type]
                audit=audit,
                correlation_id=uuid.uuid4(),
            )
        # Nothing was written, so the org is still unset and still fails closed.
        assert await dal.read_configured_mode(org) is None
        assert await dal.read_mode(org) == "observe"
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_set_mode_records_a_governance_audit_action(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        audit, repo = _audit()
        dal = OrgAutonomyModeDAL(app_db)
        await dal.set_mode(
            org_id=org, autonomy_mode="act_and_reallocate", audit=audit,
            correlation_id=uuid.uuid4(),
        )
        records = list(repo.rows)
        assert "governance.autonomy_mode_set" in [r.action_type for r in records]
        record = next(
            r for r in records if r.action_type == "governance.autonomy_mode_set"
        )
        assert "act_and_reallocate" in (record.result_reason or "")
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# RLS — the isolation that only a real database proves
# ---------------------------------------------------------------------------


@requires_app_role
@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_autonomy_read(
    app_db, app_conn, admin_conn
) -> None:
    org_a, org_b = _orgs()
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        audit, _repo = _audit()
        dal = OrgAutonomyModeDAL(app_db)
        await dal.set_mode(
            org_id=org_a, autonomy_mode="act_governed", audit=audit,
            correlation_id=uuid.uuid4(),
        )
        await dal.set_mode(
            org_id=org_b, autonomy_mode="observe", audit=audit,
            correlation_id=uuid.uuid4(),
        )

        # Bound to org_a, a raw SELECT sees ONLY org_a's row.
        async with app_db.tenant_session(org_a) as conn:
            seen = {
                r["org_id"]
                for r in await conn.fetch("SELECT org_id FROM org_autonomy_mode")
            }
        assert seen == {org_a}
        # Each org still reads its own posture correctly.
        assert await dal.read_mode(org_a) == "act_governed"
        assert await dal.read_mode(org_b) == "observe"

        # Prove the RLS-subject role is NEITHER superuser NOR the table owner —
        # otherwise RLS would be moot.
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
            "WHERE relname='org_autonomy_mode'"
        )
        assert owner != role, (
            f"{role} owns the table — owner bypasses RLS unless FORCE"
        )
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


@requires_app_role
@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_autonomy_write(app_db, admin_conn) -> None:
    """A session bound to org_a cannot write a row for org_b.

    The WITH CHECK half of tenant_isolation is what makes this true; without it
    an org could set ANOTHER org's posture, which is a governance escape, not a
    data-tidiness problem.
    """
    org_a, org_b = _orgs()
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        async with app_db.tenant_session(org_a) as conn:
            with pytest.raises(Exception):
                await conn.execute(
                    "INSERT INTO org_autonomy_mode (org_id, autonomy_mode) "
                    "VALUES ($1,$2)",
                    org_b, "act_governed",
                )
        # org_b is untouched: still unset, still fails closed.
        dal = OrgAutonomyModeDAL(app_db)
        assert await dal.read_configured_mode(org_b) is None
        assert await dal.read_mode(org_b) == "observe"
    finally:
        await _cleanup(admin_conn, [org_a, org_b])
