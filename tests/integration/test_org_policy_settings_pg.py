"""org_policy_settings integration tests — REAL Postgres, proven as the app role.

Covers what only a database can prove for migration 0035 and
``OrgPolicySettingsDAL``:
  * migration shape: FORCE RLS, tenant_isolation policy, the retention CHECK
    bound, SELECT/INSERT/UPDATE grants and NO DELETE grant;
  * fail-closed: a missing row reads as the safest-per-guardrail defaults and
    the documented retention floor, with no exceptions;
  * an explicit row reads back as exactly that row, including one that
    happens to match the defaults, which must stay distinguishable from
    "never set";
  * effective dating: a later row supersedes an earlier one; a future-dated
    row does not take effect early;
  * RLS: one org can neither READ nor WRITE another org's policy settings,
    proven as a role that is neither superuser nor the table owner;
  * every set_settings writes a governance audit record.

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
from skylize.dal.org_policy_settings import (
    DEFAULT_POLICY_SETTINGS,
    RETENTION_MIN_DAYS,
    OrgPolicySettings,
    OrgPolicySettingsDAL,
)
from skylize.events.memory_bus import InMemoryEventBus

from .conftest import APP_DB_URL, requires_app_role, requires_pg

pytestmark = pytest.mark.integration


def _orgs() -> tuple[str, str]:
    s = uuid.uuid4().hex[:8]
    return f"pol_a_{s}", f"pol_b_{s}"


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
        "DELETE FROM org_policy_settings WHERE org_id = ANY($1::text[])", orgs
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
        "WHERE c.relname='org_policy_settings' AND n.nspname=$1",
        schema,
    )
    assert row["relrowsecurity"] is True, "RLS is not enabled on org_policy_settings"
    assert row["relforcerowsecurity"] is True, (
        "RLS is not FORCED — the table owner would bypass the policy"
    )

    policy = await admin_conn.fetchval(
        "SELECT p.polname FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname='org_policy_settings' AND n.nspname=$1 "
        "AND p.polname='tenant_isolation'",
        schema,
    )
    assert policy == "tenant_isolation"

    check = await admin_conn.fetchval(
        "SELECT pg_get_constraintdef(con.oid) FROM pg_constraint con "
        "JOIN pg_class c ON c.oid = con.conrelid "
        "WHERE c.relname='org_policy_settings' AND con.contype='c'"
    )
    assert "retention_days" in check
    assert "2555" in check

    grants = {
        r["privilege_type"]
        for r in await admin_conn.fetch(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name='org_policy_settings' AND grantee='skylize_app'"
        )
    }
    assert {"SELECT", "INSERT", "UPDATE"} <= grants
    assert "DELETE" not in grants, (
        "policy settings history must not be deletable by the app role"
    )


@requires_pg
@pytest.mark.asyncio
async def test_check_constraint_rejects_retention_outside_bounds(admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        with pytest.raises(Exception) as excinfo:
            await admin_conn.execute(
                "INSERT INTO org_policy_settings "
                "(org_id, spend_cap_alert_enabled, email_domain_restriction_enabled, "
                "pii_redaction_enabled, silent_fallback_suppressed, retention_days) "
                "VALUES ($1, true, true, true, true, $2)",
                org, 1,
            )
        assert "org_policy_settings" in str(excinfo.value)
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# Fail closed and explicit reads
# ---------------------------------------------------------------------------


@requires_app_role
@pytest.mark.asyncio
async def test_missing_row_reads_as_fail_closed_defaults(app_db, admin_conn) -> None:
    """An org nobody has configured gets the safest-per-guardrail defaults and
    the documented retention floor. No exceptions."""
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        dal = OrgPolicySettingsDAL(app_db)
        assert await dal.read_settings(org) == DEFAULT_POLICY_SETTINGS
        assert DEFAULT_POLICY_SETTINGS.retention_days == RETENTION_MIN_DAYS
        # ...and the display read still reports it as UNSET, so the console can
        # tell the owner they have never made this choice.
        assert await dal.read_configured_settings(org) is None
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_explicit_row_reads_back_exactly(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        audit, _repo = _audit()
        dal = OrgPolicySettingsDAL(app_db)
        settings = OrgPolicySettings(
            spend_cap_alert_enabled=False,
            email_domain_restriction_enabled=True,
            pii_redaction_enabled=False,
            silent_fallback_suppressed=True,
            retention_days=3000,
        )
        await dal.set_settings(
            org_id=org,
            spend_cap_alert_enabled=settings.spend_cap_alert_enabled,
            email_domain_restriction_enabled=settings.email_domain_restriction_enabled,
            pii_redaction_enabled=settings.pii_redaction_enabled,
            silent_fallback_suppressed=settings.silent_fallback_suppressed,
            retention_days=settings.retention_days,
            audit=audit, correlation_id=uuid.uuid4(),
        )
        assert await dal.read_settings(org) == settings
        # A row that happened to match the defaults would still be distinct
        # from "never set" -- proven separately below with an exact-default
        # explicit write.
        assert await dal.read_configured_settings(org) == settings
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_explicit_row_matching_defaults_stays_distinguishable_from_unset(
    app_db, admin_conn
) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        audit, _repo = _audit()
        dal = OrgPolicySettingsDAL(app_db)
        await dal.set_settings(
            org_id=org,
            spend_cap_alert_enabled=DEFAULT_POLICY_SETTINGS.spend_cap_alert_enabled,
            email_domain_restriction_enabled=(
                DEFAULT_POLICY_SETTINGS.email_domain_restriction_enabled
            ),
            pii_redaction_enabled=DEFAULT_POLICY_SETTINGS.pii_redaction_enabled,
            silent_fallback_suppressed=DEFAULT_POLICY_SETTINGS.silent_fallback_suppressed,
            retention_days=DEFAULT_POLICY_SETTINGS.retention_days,
            audit=audit, correlation_id=uuid.uuid4(),
        )
        assert await dal.read_settings(org) == DEFAULT_POLICY_SETTINGS
        assert await dal.read_configured_settings(org) == DEFAULT_POLICY_SETTINGS
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
        dal = OrgPolicySettingsDAL(app_db)

        async def _set(retention: int, effective_from: datetime) -> None:
            await dal.set_settings(
                org_id=org,
                spend_cap_alert_enabled=True,
                email_domain_restriction_enabled=True,
                pii_redaction_enabled=True,
                silent_fallback_suppressed=True,
                retention_days=retention,
                audit=audit, correlation_id=uuid.uuid4(),
                effective_from=effective_from,
            )

        await _set(2555, now - timedelta(days=2))
        await _set(2600, now - timedelta(days=1))
        await _set(3000, now + timedelta(days=1))

        assert (await dal.read_settings(org)).retention_days == 2600
        assert (
            await dal.read_settings(org, now - timedelta(days=3))
        ).retention_days == RETENTION_MIN_DAYS
        assert (
            await dal.read_settings(org, now - timedelta(days=1, hours=12))
        ).retention_days == 2555
        assert (
            await dal.read_settings(org, now + timedelta(days=2))
        ).retention_days == 3000
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_set_settings_rejects_retention_outside_bounds(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        audit, _repo = _audit()
        dal = OrgPolicySettingsDAL(app_db)
        with pytest.raises(ValueError, match="retention_days must be between"):
            await dal.set_settings(
                org_id=org,
                spend_cap_alert_enabled=True,
                email_domain_restriction_enabled=True,
                pii_redaction_enabled=True,
                silent_fallback_suppressed=True,
                retention_days=1,
                audit=audit, correlation_id=uuid.uuid4(),
            )
        assert await dal.read_configured_settings(org) is None
        assert await dal.read_settings(org) == DEFAULT_POLICY_SETTINGS
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_set_settings_records_a_governance_audit_action(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        audit, repo = _audit()
        dal = OrgPolicySettingsDAL(app_db)
        await dal.set_settings(
            org_id=org,
            spend_cap_alert_enabled=False,
            email_domain_restriction_enabled=False,
            pii_redaction_enabled=False,
            silent_fallback_suppressed=False,
            retention_days=2700,
            audit=audit, correlation_id=uuid.uuid4(),
        )
        records = list(repo.rows)
        assert "governance.org_policy_settings_set" in [
            r.action_type for r in records
        ]
        record = next(
            r for r in records if r.action_type == "governance.org_policy_settings_set"
        )
        assert "2700" in (record.result_reason or "")
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# RLS — the isolation that only a real database proves
# ---------------------------------------------------------------------------


@requires_app_role
@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_policy_settings_read(
    app_db, app_conn, admin_conn
) -> None:
    org_a, org_b = _orgs()
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        audit, _repo = _audit()
        dal = OrgPolicySettingsDAL(app_db)
        await dal.set_settings(
            org_id=org_a,
            spend_cap_alert_enabled=False,
            email_domain_restriction_enabled=False,
            pii_redaction_enabled=False,
            silent_fallback_suppressed=False,
            retention_days=2600,
            audit=audit, correlation_id=uuid.uuid4(),
        )
        await dal.set_settings(
            org_id=org_b,
            spend_cap_alert_enabled=True,
            email_domain_restriction_enabled=True,
            pii_redaction_enabled=True,
            silent_fallback_suppressed=True,
            retention_days=2700,
            audit=audit, correlation_id=uuid.uuid4(),
        )

        # Bound to org_a, a raw SELECT sees ONLY org_a's row.
        async with app_db.tenant_session(org_a) as conn:
            seen = {
                r["org_id"]
                for r in await conn.fetch("SELECT org_id FROM org_policy_settings")
            }
        assert seen == {org_a}
        # Each org still reads its own settings correctly.
        assert (await dal.read_settings(org_a)).retention_days == 2600
        assert (await dal.read_settings(org_b)).retention_days == 2700

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
            "WHERE relname='org_policy_settings'"
        )
        assert owner != role, (
            f"{role} owns the table — owner bypasses RLS unless FORCE"
        )
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


@requires_app_role
@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_policy_settings_write(app_db, admin_conn) -> None:
    """A session bound to org_a cannot write a row for org_b.

    The WITH CHECK half of tenant_isolation is what makes this true; without it
    an org could set ANOTHER org's policy, which is a governance escape, not a
    data-tidiness problem.
    """
    org_a, org_b = _orgs()
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        async with app_db.tenant_session(org_a) as conn:
            with pytest.raises(Exception):
                await conn.execute(
                    "INSERT INTO org_policy_settings "
                    "(org_id, spend_cap_alert_enabled, "
                    "email_domain_restriction_enabled, pii_redaction_enabled, "
                    "silent_fallback_suppressed, retention_days) "
                    "VALUES ($1, true, true, true, true, $2)",
                    org_b, RETENTION_MIN_DAYS,
                )
        # org_b is untouched: still unset, still fails closed.
        dal = OrgPolicySettingsDAL(app_db)
        assert await dal.read_configured_settings(org_b) is None
        assert await dal.read_settings(org_b) == DEFAULT_POLICY_SETTINGS
    finally:
        await _cleanup(admin_conn, [org_a, org_b])
