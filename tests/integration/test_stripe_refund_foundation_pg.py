"""org_stripe_accounts / org_refund_authority_limits / org_refund_review_thresholds
- REAL Postgres, proven as the app role.

NOT RUN IN THIS SESSION. Written 2026-09-19 against migration 0031
(renumbered to 0036 during the origin/main sync that resolved a revision-id
collision with 0031_backfill_owner_principal.py) and
dal/stripe_accounts.py, dal/refund_limits.py by direct reading, mirroring
tests/integration/test_gcp_wif_pg.py and test_org_spend_ceiling_pg.py's
established patterns - but this session has no SKYLIZE_TEST_DB_URL /
SKYLIZE_TEST_APP_DB_URL configured, so these tests have never actually been
executed against a live database. Do not treat their presence as proof the
migration applies cleanly or that RLS holds - only a real run against
Postgres proves that (CLAUDE.md's testing section).

Covers the guarantees only a database can prove (migration 0036):
  * migration shape: FORCE RLS and a `tenant_isolation` policy on all three
    tables, the CHECK constraints, and the two org_stripe_accounts uniqueness
    guarantees (global on stripe_account_id, partial on (org_id, livemode));
  * **RLS cross-tenant isolation** on all three tables, proven as a role that
    is neither a superuser nor the table owner;
  * `org_stripe_accounts` never hard-deletes - deauthorize sets
    deauthorized_at, the row still reads back;
  * `OrgRefundLimitsDAL`'s audited-setter pattern actually writes an audit
    row, and the non-monotonic-ladder check actually refuses at the DB layer
    (not just in the in-process check).

Skipped unless SKYLIZE_TEST_DB_URL (+ SKYLIZE_TEST_APP_DB_URL) are set.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from skylize.app.audit.service import AuditService
from skylize.dal.connection import Database
from skylize.dal.refund_limits import NonMonotonicAuthorityLadder, OrgRefundLimitsDAL
from skylize.dal.stripe_accounts import PgStripeAccountRepository, StripeAccountRow
from skylize.dal.memory import InMemoryAuditRepository
from skylize.events.memory_bus import InMemoryEventBus

from .conftest import APP_DB_URL, purge_tenants, requires_app_role, requires_pg

pytestmark = pytest.mark.integration


def _orgs() -> tuple[str, str]:
    s = uuid.uuid4().hex[:8]
    return f"stripe_a_{s}", f"stripe_b_{s}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _seed_tenant(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _cleanup(admin_conn, orgs: list[str]) -> None:
    await purge_tenants(admin_conn, orgs)


def _account_row(org: str, *, livemode: bool = False, scope: str = "read_write") -> StripeAccountRow:
    now = _now()
    return StripeAccountRow(
        id=uuid.uuid4(), org_id=org, stripe_account_id=f"acct_{uuid.uuid4().hex[:16]}",
        livemode=livemode, scope=scope, connected_at=now, deauthorized_at=None,
    )


@pytest_asyncio.fixture()
async def app_db(migrated_public: None):
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")
    db = Database(APP_DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


def _audit_service() -> tuple[AuditService, InMemoryAuditRepository]:
    repo = InMemoryAuditRepository()
    return AuditService(bus=InMemoryEventBus(), repo=repo), repo


# ---------------------------------------------------------------------------
# Migration shape
# ---------------------------------------------------------------------------

@requires_pg
@pytest.mark.parametrize(
    "table",
    ["org_stripe_accounts", "org_refund_authority_limits", "org_refund_review_thresholds"],
)
async def test_migration_shape(admin_conn, pg_schema: str, table: str) -> None:
    rel = await admin_conn.fetchrow(
        "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname=$2 AND n.nspname=$1",
        pg_schema, table,
    )
    assert rel is not None, f"{table} missing"
    assert rel["relrowsecurity"] is True, "RLS not ENABLEd"
    assert rel["relforcerowsecurity"] is True, "RLS not FORCEd - owner would bypass"

    pol = await admin_conn.fetchval(
        "SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname=$2 AND n.nspname=$1 AND p.polname='tenant_isolation'",
        pg_schema, table,
    )
    assert pol == 1


@requires_pg
async def test_org_stripe_accounts_unique_indexes_exist(admin_conn, pg_schema: str) -> None:
    names = {
        r["indexname"]
        for r in await admin_conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE schemaname=$1 AND tablename=$2",
            pg_schema, "org_stripe_accounts",
        )
    }
    assert "idx_org_stripe_accounts_account_id" in names
    assert "idx_org_stripe_accounts_live_per_org" in names


@requires_pg
async def test_org_stripe_accounts_permits_one_live_and_one_test_per_org(
    admin_conn, pg_schema: str,
) -> None:
    org, _ = _orgs()
    await _seed_tenant(admin_conn, org)
    try:
        stmt = (
            "INSERT INTO org_stripe_accounts (org_id, stripe_account_id, livemode, scope) "
            "VALUES ($1,$2,$3,'read_write')"
        )
        await admin_conn.execute(stmt, org, f"acct_{uuid.uuid4().hex[:8]}", True)
        await admin_conn.execute(stmt, org, f"acct_{uuid.uuid4().hex[:8]}", False)
        # A second LIVE row for the same org must be refused.
        with pytest.raises(Exception) as exc:
            await admin_conn.execute(stmt, org, f"acct_{uuid.uuid4().hex[:8]}", True)
        assert "idx_org_stripe_accounts_live_per_org" in str(exc.value)
    finally:
        await _cleanup(admin_conn, [org])


@requires_pg
async def test_authority_level_check_constraint(admin_conn, pg_schema: str) -> None:
    org, _ = _orgs()
    await _seed_tenant(admin_conn, org)
    try:
        with pytest.raises(Exception) as exc:
            await admin_conn.execute(
                "INSERT INTO org_refund_authority_limits "
                "(org_id, currency, authority_level, max_refund_minor) "
                "VALUES ($1,'usd','ceo',1000)",
                org,
            )
        assert "authority_level_known" in str(exc.value)
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# RLS - THE HARD GATE
# ---------------------------------------------------------------------------

@requires_app_role
async def test_rls_blocks_cross_tenant_stripe_account_access(
    app_db, admin_conn, pg_schema: str,
) -> None:
    """Org A's connected Stripe account must be invisible AND immutable from
    org B, proven as `skylize_app` - neither a superuser nor the table owner."""
    org_a, org_b = _orgs()
    repo = PgStripeAccountRepository(app_db)
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)

        row_a = _account_row(org_a, livemode=False)
        await repo.insert(row_a)

        assert (await repo.get_connected(org_a, livemode=False)) is not None
        assert (await repo.get_connected(org_b, livemode=False)) is None

        # B cannot deauthorize A's account even naming A's stripe_account_id.
        changed = await repo.deauthorize(
            org_id=org_b, stripe_account_id=row_a.stripe_account_id,
            deauthorized_at=_now(),
        )
        assert changed is False
        still = await repo.get_connected(org_a, livemode=False)
        assert still is not None, "cross-tenant deauthorize must not land"

        async with app_db.tenant_session(org_b) as conn:
            leaked = await conn.fetch("SELECT id FROM org_stripe_accounts")
            assert leaked == [], "RLS policy leaked stripe accounts across tenants"
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


@requires_app_role
async def test_rls_blocks_cross_tenant_refund_limits_access(
    app_db, admin_conn, pg_schema: str,
) -> None:
    org_a, org_b = _orgs()
    dal = OrgRefundLimitsDAL(app_db)
    audit, _audit_repo = _audit_service()
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)

        await dal.set_authority_limit(
            org_id=org_a, currency="usd", authority_level="manager",
            max_refund_minor=5_000, audit=audit, correlation_id=uuid.uuid4(),
        )

        assert await dal.read_authority_limit_minor(org_a, "usd", "manager") == 5_000
        assert await dal.read_authority_limit_minor(org_b, "usd", "manager") is None

        async with app_db.tenant_session(org_b) as conn:
            leaked = await conn.fetch("SELECT org_id FROM org_refund_authority_limits")
            assert leaked == [], "RLS policy leaked refund limits across tenants"
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


# ---------------------------------------------------------------------------
# org_stripe_accounts never hard-deletes
# ---------------------------------------------------------------------------

@requires_app_role
async def test_deauthorize_sets_the_column_never_deletes_the_row(
    app_db, admin_conn, pg_schema: str,
) -> None:
    org, _ = _orgs()
    repo = PgStripeAccountRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        row = _account_row(org, livemode=False)
        await repo.insert(row)

        changed = await repo.deauthorize(
            org_id=org, stripe_account_id=row.stripe_account_id, deauthorized_at=_now(),
        )
        assert changed is True

        # No longer "connected" (deauthorized_at is set)...
        assert (await repo.get_connected(org, livemode=False)) is None
        # ...but the row itself is still there, as the audit trail.
        async with app_db.tenant_session(org) as conn:
            still = await conn.fetchrow(
                "SELECT deauthorized_at FROM org_stripe_accounts WHERE org_id=$1", org,
            )
            assert still is not None
            assert still["deauthorized_at"] is not None
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# OrgRefundLimitsDAL: audit + monotonicity, against real SQL
# ---------------------------------------------------------------------------

@requires_app_role
async def test_set_authority_limit_writes_an_audit_row(
    app_db, admin_conn, pg_schema: str,
) -> None:
    org, _ = _orgs()
    dal = OrgRefundLimitsDAL(app_db)
    audit, audit_repo = _audit_service()
    try:
        await _seed_tenant(admin_conn, org)
        await dal.set_authority_limit(
            org_id=org, currency="usd", authority_level="worker",
            max_refund_minor=1_000, audit=audit, correlation_id=uuid.uuid4(),
        )
        # Recorded through the in-memory audit repo passed to AuditService -
        # a real deployment's PgAuditRepository would land it in audit_log;
        # this asserts the DAL actually CALLED audit.record, which the
        # unit-level mock in test_stripe_gate.py cannot prove.
        assert any(
            row.action_type == "governance.refund_authority_limit_set"
            for row in audit_repo.rows
        )
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_set_authority_limit_refuses_a_non_monotonic_ladder(
    app_db, admin_conn, pg_schema: str,
) -> None:
    org, _ = _orgs()
    dal = OrgRefundLimitsDAL(app_db)
    audit, _audit_repo = _audit_service()
    try:
        await _seed_tenant(admin_conn, org)
        await dal.set_authority_limit(
            org_id=org, currency="usd", authority_level="director",
            max_refund_minor=1_000, audit=audit, correlation_id=uuid.uuid4(),
        )
        # A `manager` cap ABOVE a `director` cap is a misconfiguration.
        with pytest.raises(NonMonotonicAuthorityLadder):
            await dal.set_authority_limit(
                org_id=org, currency="usd", authority_level="manager",
                max_refund_minor=5_000, audit=audit, correlation_id=uuid.uuid4(),
            )
        # The refused write must not have landed.
        assert await dal.read_authority_limit_minor(org, "usd", "manager") is None
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_missing_row_reads_as_none_not_a_fabricated_default(
    app_db, admin_conn, pg_schema: str,
) -> None:
    org, _ = _orgs()
    dal = OrgRefundLimitsDAL(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        assert await dal.read_authority_limit_minor(org, "usd", "worker") is None
        assert await dal.read_review_threshold_minor(org, "usd") is None
    finally:
        await _cleanup(admin_conn, [org])
