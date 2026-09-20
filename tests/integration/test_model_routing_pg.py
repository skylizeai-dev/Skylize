"""model_routing_rules integration tests — REAL Postgres, proven as the app role.

Covers what only a database can prove for migration 0032 and
``ModelRoutingDAL``:
  * migration shape: FORCE RLS, tenant_isolation policy, the routing-class /
    target / fallback CHECKs, SELECT/INSERT/UPDATE grants and NO DELETE grant;
  * fail-closed: a missing row resolves every class to the identity mapping,
    with no exceptions;
  * an explicit generation reads back as exactly that generation, and a class
    left unconfigured within it stays identity;
  * effective dating: a later generation supersedes an earlier one in full; a
    future-dated generation does not take effect early;
  * RLS: one org can neither READ nor WRITE another org's routing, proven as a
    role that is neither superuser nor the table owner;
  * every set_rules writes a governance audit record;
  * the catalogue's price lookup actually joins model_pricing, both when a row
    covers the concrete model and when it (by design) does not.

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
from skylize.dal.model_routing import CATALOGUE_PROVIDER, LOGICAL_MODELS, ModelRoutingDAL
from skylize.events.memory_bus import InMemoryEventBus

from .conftest import APP_DB_URL, purge_tenants, requires_app_role, requires_pg

pytestmark = pytest.mark.integration


class _FakeSettings:
    """Concrete ids distinct from real provider ids, so a query that somehow
    matched the wrong column would show up as a broken test, not a coincidence."""

    llm_model_default = "test-concrete-default"
    llm_model_fast = "test-concrete-fast"
    llm_model_reasoning = "test-concrete-reasoning"


def _orgs() -> tuple[str, str]:
    s = uuid.uuid4().hex[:8]
    return f"route_a_{s}", f"route_b_{s}"


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
    await purge_tenants(admin_conn, orgs)


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
        "WHERE c.relname='model_routing_rules' AND n.nspname=$1",
        schema,
    )
    assert row["relrowsecurity"] is True, "RLS is not enabled on model_routing_rules"
    assert row["relforcerowsecurity"] is True, (
        "RLS is not FORCED — the table owner would bypass the policy"
    )

    policy = await admin_conn.fetchval(
        "SELECT p.polname FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname='model_routing_rules' AND n.nspname=$1 "
        "AND p.polname='tenant_isolation'",
        schema,
    )
    assert policy == "tenant_isolation"

    # Every CHECK on the table, concatenated, must name exactly the three
    # logical models for both routing_class and target_logical_model -- a
    # fourth value cannot enter through SQL even if some caller skips the
    # DAL's own validation.
    checks = " ".join(
        r["def"]
        for r in await admin_conn.fetch(
            "SELECT pg_get_constraintdef(con.oid) AS def FROM pg_constraint con "
            "JOIN pg_class c ON c.oid = con.conrelid "
            "WHERE c.relname='model_routing_rules' AND con.contype='c'"
        )
    )
    for logical in LOGICAL_MODELS:
        assert logical in checks, f"{logical} missing from a CHECK constraint"

    grants = {
        r["privilege_type"]
        for r in await admin_conn.fetch(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name='model_routing_rules' AND grantee='skylize_app'"
        )
    }
    assert {"SELECT", "INSERT", "UPDATE"} <= grants
    assert "DELETE" not in grants, (
        "routing history must not be deletable by the app role"
    )


@requires_pg
@pytest.mark.asyncio
async def test_check_constraint_rejects_an_unknown_routing_class(admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        with pytest.raises(Exception) as excinfo:
            await admin_conn.execute(
                "INSERT INTO model_routing_rules "
                "(org_id, routing_class, target_logical_model) VALUES ($1,$2,$3)",
                org, "atlas-4-frontier", "fast",
            )
        assert "model_routing_rules" in str(excinfo.value)
    finally:
        await _cleanup(admin_conn, [org])


@requires_pg
@pytest.mark.asyncio
async def test_check_constraint_rejects_an_unknown_target(admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        with pytest.raises(Exception):
            await admin_conn.execute(
                "INSERT INTO model_routing_rules "
                "(org_id, routing_class, target_logical_model) VALUES ($1,$2,$3)",
                org, "fast", "nova-2-fast",
            )
    finally:
        await _cleanup(admin_conn, [org])


@requires_pg
@pytest.mark.asyncio
async def test_check_constraint_rejects_a_fallback_equal_to_target(admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        with pytest.raises(Exception):
            await admin_conn.execute(
                "INSERT INTO model_routing_rules "
                "(org_id, routing_class, target_logical_model, fallback_logical_model) "
                "VALUES ($1,$2,$3,$3)",
                org, "fast", "default",
            )
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# Fail closed and explicit reads
# ---------------------------------------------------------------------------


@requires_app_role
@pytest.mark.asyncio
async def test_missing_rows_resolve_to_identity_for_every_class(
    app_db, admin_conn
) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        dal = ModelRoutingDAL(app_db)
        rules = await dal.read_rules(org)
        assert [r.routing_class for r in rules] == list(LOGICAL_MODELS)
        for rule in rules:
            assert rule.target_logical_model == rule.routing_class
            assert rule.fallback_logical_model is None
            assert rule.configured is False
        assert await dal.read_configured_rules(org) == []
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_a_configured_generation_reads_back_exactly(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        audit, _repo = _audit()
        dal = ModelRoutingDAL(app_db)
        await dal.set_rules(
            org_id=org,
            rules={"reasoning": ("fast", "default"), "fast": ("default", None)},
            audit=audit,
            correlation_id=uuid.uuid4(),
        )
        rules = {r.routing_class: r for r in await dal.read_rules(org)}
        assert rules["reasoning"].target_logical_model == "fast"
        assert rules["reasoning"].fallback_logical_model == "default"
        assert rules["reasoning"].configured is True
        assert rules["fast"].target_logical_model == "default"
        assert rules["fast"].configured is True
        # The class this generation did not touch stays the identity mapping.
        assert rules["default"].target_logical_model == "default"
        assert rules["default"].configured is False
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_later_generation_supersedes_and_future_one_does_not_apply_early(
    app_db, admin_conn
) -> None:
    org, _ = _orgs()
    now = datetime.now(timezone.utc)
    try:
        await _seed_tenant(admin_conn, org)
        audit, _repo = _audit()
        dal = ModelRoutingDAL(app_db)
        await dal.set_rules(
            org_id=org, rules={"fast": ("default", None)}, audit=audit,
            correlation_id=uuid.uuid4(), effective_from=now - timedelta(days=2),
        )
        await dal.set_rules(
            org_id=org, rules={"fast": ("reasoning", None)}, audit=audit,
            correlation_id=uuid.uuid4(), effective_from=now - timedelta(days=1),
        )
        await dal.set_rules(
            org_id=org, rules={"fast": ("default", "reasoning")}, audit=audit,
            correlation_id=uuid.uuid4(), effective_from=now + timedelta(days=1),
        )

        async def _fast_target(at: datetime | None) -> str:
            rules = {r.routing_class: r for r in await dal.read_rules(org, at)}
            return rules["fast"].target_logical_model

        # In force now: the most recent generation at or before now.
        assert await _fast_target(None) == "reasoning"
        # Before any generation existed the org still fails closed to identity.
        assert await _fast_target(now - timedelta(days=3)) == "fast"
        # The older generation is still what was in force back then.
        assert await _fast_target(now - timedelta(days=1, hours=12)) == "default"
        # The future-dated generation does not take effect early.
        assert await _fast_target(now + timedelta(days=2)) == "default"
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_set_rules_rejects_an_unknown_value(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        audit, _repo = _audit()
        dal = ModelRoutingDAL(app_db)
        with pytest.raises(ValueError, match="target_logical_model must be one of"):
            await dal.set_rules(
                org_id=org,
                rules={"fast": ("atlas-4-frontier", None)},  # type: ignore[dict-item]
                audit=audit,
                correlation_id=uuid.uuid4(),
            )
        # Nothing was written, so the org is still unset and still fails closed.
        assert await dal.read_configured_rules(org) == []
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_set_rules_records_a_governance_audit_action(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        audit, repo = _audit()
        dal = ModelRoutingDAL(app_db)
        await dal.set_rules(
            org_id=org, rules={"reasoning": ("fast", None)}, audit=audit,
            correlation_id=uuid.uuid4(),
        )
        records = list(repo.rows)
        assert "governance.model_routing_set" in [r.action_type for r in records]
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# Catalogue — real price lookup against model_pricing
# ---------------------------------------------------------------------------


@requires_app_role
@pytest.mark.asyncio
async def test_catalogue_reports_none_when_model_pricing_has_no_row(
    app_db, admin_conn
) -> None:
    """model_pricing is seeded EMPTY by design (migration 0012). Proven here
    against the real table, not a fake."""
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        dal = ModelRoutingDAL(app_db)
        entries = await dal.read_catalogue(org, _FakeSettings())
        assert [e.pricing for e in entries] == [None, None, None]
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_catalogue_reports_a_real_seeded_price(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        await admin_conn.execute(
            """
            INSERT INTO model_pricing (
                org_id, provider, model, input_price_micros_per_mtok,
                output_price_micros_per_mtok, version, effective_from
            ) VALUES (NULL, $1, $2, 3000000, 15000000, 1, now() - interval '1 day')
            """,
            CATALOGUE_PROVIDER,
            _FakeSettings.llm_model_fast,
        )
        dal = ModelRoutingDAL(app_db)
        entries = {e.logical_name: e for e in await dal.read_catalogue(org, _FakeSettings())}
        assert entries["fast"].pricing is not None
        assert entries["fast"].pricing.input_price_micros_per_mtok == 3000000
        assert entries["default"].pricing is None
    finally:
        await admin_conn.execute(
            "DELETE FROM model_pricing WHERE provider=$1 AND model=$2",
            CATALOGUE_PROVIDER, _FakeSettings.llm_model_fast,
        )
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# RLS — the isolation that only a real database proves
# ---------------------------------------------------------------------------


@requires_app_role
@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_routing_read(app_db, app_conn, admin_conn) -> None:
    org_a, org_b = _orgs()
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        audit, _repo = _audit()
        dal = ModelRoutingDAL(app_db)
        await dal.set_rules(
            org_id=org_a, rules={"fast": ("reasoning", None)}, audit=audit,
            correlation_id=uuid.uuid4(),
        )
        await dal.set_rules(
            org_id=org_b, rules={"fast": ("default", None)}, audit=audit,
            correlation_id=uuid.uuid4(),
        )

        # Bound to org_a, a raw SELECT sees ONLY org_a's row.
        async with app_db.tenant_session(org_a) as conn:
            seen = {
                r["org_id"]
                for r in await conn.fetch("SELECT org_id FROM model_routing_rules")
            }
        assert seen == {org_a}

        # Each org still reads its own routing correctly.
        a_rules = {r.routing_class: r for r in await dal.read_rules(org_a)}
        b_rules = {r.routing_class: r for r in await dal.read_rules(org_b)}
        assert a_rules["fast"].target_logical_model == "reasoning"
        assert b_rules["fast"].target_logical_model == "default"

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
            "WHERE relname='model_routing_rules'"
        )
        assert owner != role, (
            f"{role} owns the table — owner bypasses RLS unless FORCE"
        )
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


@requires_app_role
@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_routing_write(app_db, admin_conn) -> None:
    """A session bound to org_a cannot write a row for org_b.

    The WITH CHECK half of tenant_isolation is what makes this true; without it
    an org could redirect ANOTHER org's traffic, which is a governance and
    billing escape, not a data-tidiness problem.
    """
    org_a, org_b = _orgs()
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        async with app_db.tenant_session(org_a) as conn:
            with pytest.raises(Exception):
                await conn.execute(
                    "INSERT INTO model_routing_rules "
                    "(org_id, routing_class, target_logical_model) VALUES ($1,$2,$3)",
                    org_b, "fast", "reasoning",
                )
        # org_b is untouched: still unset, still fails closed to identity.
        dal = ModelRoutingDAL(app_db)
        assert await dal.read_configured_rules(org_b) == []
    finally:
        await _cleanup(admin_conn, [org_a, org_b])
