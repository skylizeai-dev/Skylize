"""Cost-ledger integration tests — REAL Postgres, proven as the RLS-subject role.

Covers the guarantees that only a database can prove (ADR-0006):
  * reconciliation: recorded calls SUM exactly to the expected total (tolerance 0);
  * idempotency: the same call recorded twice yields one row;
  * RLS: tenant A cannot read tenant B's rows (as the NON-SUPERUSER app role);
  * append-only: UPDATE and DELETE are rejected at the DB level;
  * pricing-version: a later price change does not alter a historical row's cost.

Skipped unless SKYLIZE_TEST_DB_URL (+ APP_DB_URL) are set. All prices are
EXPLICITLY SYNTHETIC (SYNTH_*), never real provider prices.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from skylize.dal.connection import Database
from skylize.dal.cost_ledger import CostLedgerDAL, CostObservation, PricingNotFound

from .conftest import APP_DB_URL, requires_app_role, requires_pg

pytestmark = pytest.mark.integration

# Synthetic µ/Mtok prices — NOT real provider prices. 3 µ/tok in, 15 µ/tok out.
SYNTH_IN = 3_000_000
SYNTH_OUT = 15_000_000
_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _orgs() -> tuple[str, str]:
    s = uuid.uuid4().hex[:8]
    return f"cost_a_{s}", f"cost_b_{s}"


async def _seed_tenant(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _seed_global_price(
    admin_conn, provider: str, model: str, *, version: int = 1,
    in_p: int = SYNTH_IN, out_p: int = SYNTH_OUT, effective_from: datetime = _EPOCH,
) -> None:
    await admin_conn.execute(
        """
        INSERT INTO model_pricing (org_id, provider, model,
            input_price_micros_per_mtok, output_price_micros_per_mtok,
            currency, version, effective_from)
        VALUES (NULL, $1, $2, $3, $4, 'USD', $5, $6)
        """,
        provider, model, in_p, out_p, version, effective_from,
    )


def _obs(org: str, provider: str, model: str, *, i: int, o: int, key: str,
         occurred_at: datetime | None = None) -> CostObservation:
    return CostObservation(
        org_id=org,
        correlation_id=uuid.uuid4(),
        agent_id="agent_x",
        run_id=uuid.uuid4(),
        provider=provider,
        model=model,
        input_tokens=i,
        output_tokens=o,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        billing_period="2026-07",
        idempotency_key=key,
    )


async def _cleanup(admin_conn, orgs: list[str], provider: str) -> None:
    # ai_cost_ledger is append-only (row DELETE is blocked by the trigger even for
    # a superuser); TRUNCATE bypasses row-level triggers and clears the FK so the
    # tenant rows can then be removed. Only this suite touches ai_cost_ledger.
    await admin_conn.execute("TRUNCATE ai_cost_ledger")
    await admin_conn.execute("DELETE FROM model_pricing WHERE provider = $1", provider)
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
# Reconciliation — exact, tolerance zero (through the real DAL + Postgres SUM).
# ---------------------------------------------------------------------------

@requires_app_role
async def test_reconciliation_sum_is_exact(app_db, admin_conn) -> None:
    org, _ = _orgs()
    provider, model = f"synthprov_{uuid.uuid4().hex[:6]}", "synth-model"
    try:
        await _seed_tenant(admin_conn, org)
        await _seed_global_price(admin_conn, provider, model)
        dal = CostLedgerDAL(app_db)

        calls = [(1_000, 500), (2_500, 100), (0, 4_000), (777, 333)]
        expected = sum(i * 3 + o * 15 for i, o in calls)  # exact micros
        for n, (i, o) in enumerate(calls):
            rec = await dal.record_cost(_obs(org, provider, model, i=i, o=o, key=f"k{n}"))
            assert rec.inserted is True

        total = await dal.period_total_micros(org, provider, "2026-07")
        assert total == expected  # tolerance ZERO
    finally:
        await _cleanup(admin_conn, [org], provider)


# ---------------------------------------------------------------------------
# Idempotency — the same call recorded twice yields exactly one row.
# ---------------------------------------------------------------------------

@requires_app_role
async def test_idempotency_same_key_one_row(app_db, admin_conn) -> None:
    org, _ = _orgs()
    provider, model = f"synthprov_{uuid.uuid4().hex[:6]}", "synth-model"
    try:
        await _seed_tenant(admin_conn, org)
        await _seed_global_price(admin_conn, provider, model)
        dal = CostLedgerDAL(app_db)

        obs = _obs(org, provider, model, i=1_000, o=1_000, key="dup-key")
        first = await dal.record_cost(obs)
        second = await dal.record_cost(obs)  # retried write, same idempotency key

        assert first.inserted is True
        assert second.inserted is False
        assert first.entry_id == second.entry_id
        assert first.cost_micros == second.cost_micros

        async with app_db.tenant_session(org) as conn:
            count = await conn.fetchval("SELECT count(*) FROM ai_cost_ledger")
        assert count == 1  # never double-charged
    finally:
        await _cleanup(admin_conn, [org], provider)


# ---------------------------------------------------------------------------
# RLS — tenant A cannot read tenant B's rows (as the app role).
# ---------------------------------------------------------------------------

@requires_app_role
async def test_rls_blocks_cross_tenant_read(app_db, admin_conn) -> None:
    org_a, org_b = _orgs()
    provider, model = f"synthprov_{uuid.uuid4().hex[:6]}", "synth-model"
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        await _seed_global_price(admin_conn, provider, model)
        dal = CostLedgerDAL(app_db)

        await dal.record_cost(_obs(org_a, provider, model, i=1_000, o=0, key="a1"))
        await dal.record_cost(_obs(org_a, provider, model, i=2_000, o=0, key="a2"))
        await dal.record_cost(_obs(org_b, provider, model, i=9_000, o=0, key="b1"))

        # Bound to org_a, a raw SELECT sees ONLY org_a's rows.
        async with app_db.tenant_session(org_a) as conn:
            seen = {r["org_id"] for r in await conn.fetch("SELECT org_id FROM ai_cost_ledger")}
        assert seen == {org_a}

        # And the per-tenant reconciliation totals are isolated.
        assert await dal.period_total_micros(org_a, provider, "2026-07") == (1_000 + 2_000) * 3
        assert await dal.period_total_micros(org_b, provider, "2026-07") == 9_000 * 3
    finally:
        await _cleanup(admin_conn, [org_a, org_b], provider)


# ---------------------------------------------------------------------------
# Append-only — UPDATE and DELETE rejected at the DB level.
# ---------------------------------------------------------------------------

@requires_app_role
async def test_append_only_update_and_delete_rejected(app_db, admin_conn) -> None:
    import asyncpg

    org, _ = _orgs()
    provider, model = f"synthprov_{uuid.uuid4().hex[:6]}", "synth-model"
    try:
        await _seed_tenant(admin_conn, org)
        await _seed_global_price(admin_conn, provider, model)
        dal = CostLedgerDAL(app_db)
        await dal.record_cost(_obs(org, provider, model, i=1_000, o=1_000, key="imm"))

        async with app_db.tenant_session(org) as conn:
            with pytest.raises(asyncpg.PostgresError, match="append-only|permission denied"):
                await conn.execute("UPDATE ai_cost_ledger SET cost_micros = 0")
        async with app_db.tenant_session(org) as conn:
            with pytest.raises(asyncpg.PostgresError, match="append-only|permission denied"):
                await conn.execute("DELETE FROM ai_cost_ledger")
    finally:
        await _cleanup(admin_conn, [org], provider)


# ---------------------------------------------------------------------------
# Corrections happen via reversing entries, never UPDATE.
# ---------------------------------------------------------------------------

@requires_app_role
async def test_reversal_nets_to_zero(app_db, admin_conn) -> None:
    org, _ = _orgs()
    provider, model = f"synthprov_{uuid.uuid4().hex[:6]}", "synth-model"
    try:
        await _seed_tenant(admin_conn, org)
        await _seed_global_price(admin_conn, provider, model)
        dal = CostLedgerDAL(app_db)

        charge = await dal.record_cost(_obs(org, provider, model, i=1_000, o=1_000, key="c1"))
        assert charge.cost_micros > 0
        rev = await dal.reverse_entry(org, charge.entry_id, idempotency_key="rev:c1")
        assert rev.cost_micros == -charge.cost_micros

        # Net cost after a charge + its reversal is exactly zero; two rows remain.
        assert await dal.period_total_micros(org, provider, "2026-07") == 0
        async with app_db.tenant_session(org) as conn:
            count = await conn.fetchval("SELECT count(*) FROM ai_cost_ledger")
        assert count == 2
    finally:
        await _cleanup(admin_conn, [org], provider)


# ---------------------------------------------------------------------------
# Pricing-version — a later price change does not alter historical row costs.
# ---------------------------------------------------------------------------

@requires_app_role
async def test_price_change_does_not_alter_history(app_db, admin_conn) -> None:
    org, _ = _orgs()
    provider, model = f"synthprov_{uuid.uuid4().hex[:6]}", "synth-model"
    try:
        await _seed_tenant(admin_conn, org)
        # v1 active from the epoch.
        await _seed_global_price(admin_conn, provider, model, version=1,
                                 in_p=SYNTH_IN, out_p=SYNTH_OUT)
        dal = CostLedgerDAL(app_db)

        rec = await dal.record_cost(_obs(org, provider, model, i=1_000, o=1_000, key="hist"))
        expected_v1 = 1_000 * 3 + 1_000 * 15
        assert rec.cost_micros == expected_v1

        # A price CHANGE lands: close v1, add v2 at double the price, active now.
        cut = datetime.now(timezone.utc)
        await admin_conn.execute(
            "UPDATE model_pricing SET effective_to = $1 "
            "WHERE provider = $2 AND version = 1", cut, provider,
        )
        await _seed_global_price(admin_conn, provider, model, version=2,
                                 in_p=SYNTH_IN * 2, out_p=SYNTH_OUT * 2,
                                 effective_from=cut)

        # The already-recorded row is unchanged: snapshot version 1, original cost.
        async with app_db.tenant_session(org) as conn:
            row = await conn.fetchrow(
                "SELECT pricing_version, cost_micros FROM ai_cost_ledger WHERE entry_id = $1",
                rec.entry_id,
            )
        assert row["pricing_version"] == 1
        assert row["cost_micros"] == expected_v1
    finally:
        await _cleanup(admin_conn, [org], provider)


# ---------------------------------------------------------------------------
# Concurrency — N PARALLEL record_cost calls produce a ledger total exactly
# equal to the sum of the individual costs (tolerance zero, no lost writes).
# ---------------------------------------------------------------------------

@requires_app_role
async def test_concurrent_writes_sum_exactly(app_db, admin_conn) -> None:
    import asyncio

    org, _ = _orgs()
    provider, model = f"synthprov_{uuid.uuid4().hex[:6]}", "synth-model"
    try:
        await _seed_tenant(admin_conn, org)
        await _seed_global_price(admin_conn, provider, model)
        dal = CostLedgerDAL(app_db)

        calls = [(100 * (n + 1), 37 * (n + 1)) for n in range(16)]
        expected = sum(i * 3 + o * 15 for i, o in calls)  # exact micros

        records = await asyncio.gather(*(
            dal.record_cost(_obs(org, provider, model, i=i, o=o, key=f"par{n}"))
            for n, (i, o) in enumerate(calls)
        ))
        assert all(rec.inserted for rec in records)
        assert sum(rec.cost_micros for rec in records) == expected

        total = await dal.period_total_micros(org, provider, "2026-07")
        assert total == expected  # tolerance ZERO under concurrency
    finally:
        await _cleanup(admin_conn, [org], provider)


# ---------------------------------------------------------------------------
# Fail closed — no active price means no fabricated cost.
# ---------------------------------------------------------------------------

@requires_app_role
async def test_missing_price_raises(app_db, admin_conn) -> None:
    org, _ = _orgs()
    provider, model = f"synthprov_{uuid.uuid4().hex[:6]}", "synth-model"
    try:
        await _seed_tenant(admin_conn, org)  # no price seeded
        dal = CostLedgerDAL(app_db)
        with pytest.raises(PricingNotFound):
            await dal.record_cost(_obs(org, provider, model, i=1, o=1, key="np"))
    finally:
        await _cleanup(admin_conn, [org], provider)


# ---------------------------------------------------------------------------
# Schema guard: the covering index the pre-egress aggregate depends on
# ---------------------------------------------------------------------------


@requires_pg
async def test_org_period_aggregate_has_its_covering_index(
    migrated_public, admin_conn
) -> None:
    """Migration 0016's index must survive.

    ``org_period_total_micros`` runs before EVERY LLM generation. Measured on
    PostgreSQL 16 with 200,000 rows in the (org, period) under test, the planner
    without this index abandons ``idx_ai_cost_ledger_reconcile`` and falls back
    to a Parallel Seq Scan of the WHOLE table (11,583 buffers, every tenant's
    heap pages); with it the same query is an Index Only Scan with
    ``Heap Fetches: 0`` (1,209 buffers). The INCLUDE column is what makes the
    heap unnecessary, so a "tidy-up" that drops it back to a plain two-column
    index would silently undo the fix.
    """
    definition = await admin_conn.fetchval(
        "SELECT indexdef FROM pg_indexes "
        "WHERE tablename = 'ai_cost_ledger' AND indexname = $1",
        "idx_ai_cost_ledger_org_period",
    )
    assert definition is not None, "migration 0016's covering index is missing"
    normalized = " ".join(definition.lower().split())
    assert "(org_id, billing_period)" in normalized
    assert "include (cost_micros)" in normalized
