"""Pg-backed Decision Engine stores against REAL Postgres as the app role.

The whole point of migration 0011: the engine's budget ceilings and idempotency
markers must survive a process restart, which the in-memory defaults never
could. A "restart" here is literal — the first `Database` pool is closed and a
brand-new pool + engine is built, so anything that survives did so in Postgres.

Proves, with no mocks:
  (a) a budget ceiling persists across an engine restart (budget_ledger);
  (b) the processed-event store dedupes a replayed event across a restart
      (decision_processed_events), both at the store level and through a full
      DecisionEngine replay;
  and that the 0011 RLS policy actually binds for the new table.

Skipped unless SKYLIZE_TEST_DB_URL + SKYLIZE_TEST_APP_DB_URL are set.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from skylize.app.audit.service import AuditService
from skylize.app.decision_engine import DecisionEngine
from skylize.config import Settings
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.connection import Database
from skylize.dal.decision_stores import PgCapitalRepository, PgProcessedEventStore
from skylize.dal.memory import InMemoryAuditRepository
from skylize.dal.ports import BudgetCeiling
from skylize.events.memory_bus import InMemoryEventBus
from skylize.schemas.events.creative import CreativeReviewRequested

from .conftest import APP_DB_URL, requires_app_role

pytestmark = pytest.mark.integration


async def _seed_tenant(conn, org: str) -> None:
    await conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _cleanup(admin_conn, *orgs: str) -> None:
    await admin_conn.execute(
        "DELETE FROM decision_processed_events WHERE org_id=ANY($1::text[])", list(orgs)
    )
    await admin_conn.execute(
        "DELETE FROM budget_ledger WHERE org_id=ANY($1::text[])", list(orgs)
    )
    await admin_conn.execute("DELETE FROM tenants WHERE org_id=ANY($1::text[])", list(orgs))


@pytest_asyncio.fixture()
async def app_db(migrated_public: None):
    """A `Database` bound to the non-superuser app role — the RLS-subject path."""
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")
    db = Database(APP_DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


def _engine(bus: InMemoryEventBus, db: Database) -> DecisionEngine:
    """A DecisionEngine over the DURABLE Pg stores (bus/audit stay in-memory —
    the stores are what is under test)."""
    audit = AuditService(bus, InMemoryAuditRepository())
    return DecisionEngine(
        bus, MVP_REGISTRY, audit, Settings(backend="memory"),
        capital=PgCapitalRepository(db), processed=PgProcessedEventStore(db),
    )


def _spend_event(org: str) -> CreativeReviewRequested:
    return CreativeReviewRequested(
        tenant_id=org,
        partition_key="brief:durable",
        department="creative",
        source_agent_id="vp_creative",
        correlation_id=uuid.uuid4(),
        payload=CreativeReviewRequested.Payload(
            brief_id=uuid.uuid4(),
            asset_ids=[uuid.uuid4()],
            proposed_action="stage",
            proposed_spend_minor_units=2_000,
        ),
    )


# -- (a) budget ceiling durability -------------------------------------------

@requires_app_role
@pytest.mark.asyncio
async def test_ceiling_survives_restart(app_db, admin_conn) -> None:
    org = f"org_cap_{uuid.uuid4().hex[:8]}"
    await _seed_tenant(admin_conn, org)
    try:
        await PgCapitalRepository(app_db).set_ceiling(
            BudgetCeiling(org, "creative", ceiling_minor_units=10_000, committed_minor_units=100)
        )
        # Restart: tear the pool down entirely; a fresh one must still see it.
        await app_db.close()
        db2 = Database(APP_DB_URL)
        await db2.connect()
        try:
            ceiling = await PgCapitalRepository(db2).get_ceiling(org, "creative")
        finally:
            await db2.close()
        assert ceiling is not None, "ceiling vanished across restart — not durable"
        assert ceiling.ceiling_minor_units == 10_000
        assert ceiling.committed_minor_units == 100
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
@pytest.mark.asyncio
async def test_set_ceiling_upsert_is_idempotent(app_db, admin_conn) -> None:
    """Re-seeding the same (org, scope, period) updates in place — one row."""
    org = f"org_cap_{uuid.uuid4().hex[:8]}"
    await _seed_tenant(admin_conn, org)
    try:
        repo = PgCapitalRepository(app_db)
        await repo.set_ceiling(BudgetCeiling(org, "creative", ceiling_minor_units=5_000))
        await repo.set_ceiling(BudgetCeiling(org, "creative", ceiling_minor_units=9_000))
        rows = await admin_conn.fetchval(
            "SELECT COUNT(*) FROM budget_ledger WHERE org_id=$1 AND scope=$2", org, "creative"
        )
        assert rows == 1
        ceiling = await repo.get_ceiling(org, "creative")
        assert ceiling is not None and ceiling.ceiling_minor_units == 9_000
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
@pytest.mark.asyncio
async def test_engine_approves_within_pg_ceiling(app_db, admin_conn) -> None:
    """The evaluator's stage-4 capital check reads the durable ledger."""
    org = f"org_cap_{uuid.uuid4().hex[:8]}"
    await _seed_tenant(admin_conn, org)
    try:
        await PgCapitalRepository(app_db).set_ceiling(
            BudgetCeiling(org, "creative", ceiling_minor_units=10_000)
        )
        bus = InMemoryEventBus()
        await _engine(bus, app_db)._handle_event(_spend_event(org))
        assert bus.published_of_type("decision.approved"), (
            "spend within the Pg-stored ceiling should approve"
        )
    finally:
        await _cleanup(admin_conn, org)


# -- (b) idempotency durability ----------------------------------------------

@requires_app_role
@pytest.mark.asyncio
async def test_processed_marker_survives_restart(app_db, admin_conn) -> None:
    org = f"org_idem_{uuid.uuid4().hex[:8]}"
    await _seed_tenant(admin_conn, org)
    key = str(uuid.uuid4())
    try:
        await PgProcessedEventStore(app_db).mark_processed(key, "approved", org_id=org)
        # Replayed marker must not error (ON CONFLICT DO NOTHING).
        await PgProcessedEventStore(app_db).mark_processed(key, "rejected", org_id=org)

        await app_db.close()
        db2 = Database(APP_DB_URL)
        await db2.connect()
        try:
            assert await PgProcessedEventStore(db2).is_processed(key, org_id=org)
        finally:
            await db2.close()
        # First outcome sticks under redelivery.
        outcome = await admin_conn.fetchval(
            "SELECT outcome FROM decision_processed_events WHERE org_id=$1 AND key=$2",
            org, key,
        )
        assert outcome == "approved"
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
@pytest.mark.asyncio
async def test_engine_restart_dedupes_replayed_event(app_db, admin_conn) -> None:
    """Full-path proof: the SAME event replayed into a REBUILT engine (fresh
    pool, fresh in-memory state) yields zero additional decisions."""
    org = f"org_idem_{uuid.uuid4().hex[:8]}"
    await _seed_tenant(admin_conn, org)
    try:
        await PgCapitalRepository(app_db).set_ceiling(
            BudgetCeiling(org, "creative", ceiling_minor_units=10_000)
        )
        event = _spend_event(org)

        bus1 = InMemoryEventBus()
        await _engine(bus1, app_db)._handle_event(event)
        assert len(bus1.published_of_type("decision.approved")) == 1

        # Restart: new pool, new bus, new engine — only Postgres remembers.
        await app_db.close()
        db2 = Database(APP_DB_URL)
        await db2.connect()
        try:
            bus2 = InMemoryEventBus()
            await _engine(bus2, db2)._handle_event(event)
        finally:
            await db2.close()
        assert bus2.published_of_type("decision.evaluated") == [], (
            "replayed event was re-decided after restart — idempotency not durable"
        )
        assert bus2.published_of_type("decision.approved") == []
    finally:
        await _cleanup(admin_conn, org)


# -- RLS binds for the 0011 table ---------------------------------------------

@requires_app_role
@pytest.mark.asyncio
async def test_rls_isolates_processed_events_across_tenants(app_db, admin_conn) -> None:
    suffix = uuid.uuid4().hex[:8]
    org_a, org_b = f"org_a_{suffix}", f"org_b_{suffix}"
    await _seed_tenant(admin_conn, org_a)
    await _seed_tenant(admin_conn, org_b)
    key = str(uuid.uuid4())
    try:
        store = PgProcessedEventStore(app_db)
        await store.mark_processed(key, "approved", org_id=org_a)

        # Scoped to org B, org A's marker is invisible — the same key reads
        # unprocessed, and a raw count under org B's session sees nothing.
        assert not await store.is_processed(key, org_id=org_b)
        async with app_db.tenant_session(org_b) as conn:
            visible = await conn.fetchval(
                "SELECT COUNT(*) FROM decision_processed_events WHERE key=$1", key
            )
        assert visible == 0, "RLS leak: org B can read org A's processed-event marker"

        assert await store.is_processed(key, org_id=org_a)
    finally:
        await _cleanup(admin_conn, org_a, org_b)
