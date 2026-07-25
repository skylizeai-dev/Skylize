"""Transactional budget reservation on the OPA decision path — PROPERTIES, against
REAL Postgres (capital_allocation.md §4).

The reservation is the thing that makes "budget-capped execution" real: before this,
the ceiling was READ (stage 4) but nothing ever WROTE ``budget_ledger.committed``, so
two proposals that each fit the ceiling in isolation could both approve and jointly
overshoot it. These tests assert the PROPERTIES that fix that, not the code path:

  * concurrency — N proposals against one ceiling never jointly exceed spendable, and
    the losers DEFER rather than approve (this is the property that FAILS pre-fix,
    because with no reservation every proposal approves and committed stays 0);
  * a reservation that would breach the ceiling converts the outcome to a deferral
    (SPEND_OVER_CEILING), never approve-then-fail;
  * the reservation and the decisions row commit or roll back TOGETHER;
  * RLS — tenant A cannot reserve against tenant B's ledger;
  * idempotency — a redelivered decision reserves exactly once;
  * the release primitive reverses a reservation.

Row locking cannot be proven against an in-memory mock, so every test here runs the
non-superuser ``skylize_app`` role against real Postgres and SKIPS when the infra env
vars are absent (matching tests/integration/test_decision_engine_stores.py).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio

from skylize.dal.connection import Database
from skylize.decision_engine.capital_dal import CapitalDAL
from skylize.decision_engine.config import DecisionEngineSettings
from skylize.decision_engine.models import (
    CapitalCheckResult,
    DecisionOutcome,
    DecisionResult,
    EvaluationStage,
    EvaluationStepRecord,
)
from skylize.decision_engine.publisher import DecisionEventPublisher

from .conftest import APP_DB_URL, requires_app_role

pytestmark = pytest.mark.integration

_PERIOD = "2026-07"
_DEPARTMENT = "creative"
_SCOPE = f"department:{_DEPARTMENT}"


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def app_db(migrated_public: None):
    """A ``Database`` bound to the non-superuser app role — the RLS-subject path that
    proves row locking and tenant isolation actually hold."""
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")
    db = Database(APP_DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


def _settings() -> DecisionEngineSettings:
    return DecisionEngineSettings(
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",
        database_url=APP_DB_URL or "postgresql://unused",
        capital_reserve_floor_pct=0.15,
    )


async def _seed_tenant(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _seed_ledger(
    admin_conn, org: str, *, ceiling: int, committed: int = 0,
    scope: str = _SCOPE, spent: int = 0,
) -> None:
    await admin_conn.execute(
        """
        INSERT INTO budget_ledger (org_id, scope, ceiling, committed, spent, period)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        org, scope, ceiling, committed, spent, _PERIOD,
    )


async def _committed(admin_conn, org: str, scope: str = _SCOPE) -> int:
    return await admin_conn.fetchval(
        "SELECT committed FROM budget_ledger WHERE org_id=$1 AND scope=$2", org, scope
    )


async def _cleanup(admin_conn, *orgs: str) -> None:
    orgs_list = list(orgs)
    await admin_conn.execute(
        "DELETE FROM decision_outbox WHERE tenant_id = ANY($1::text[])", orgs_list
    )
    await admin_conn.execute(
        "DELETE FROM decisions WHERE org_id = ANY($1::text[])", orgs_list
    )
    await admin_conn.execute(
        "DELETE FROM budget_ledger WHERE org_id = ANY($1::text[])", orgs_list
    )
    await admin_conn.execute("DELETE FROM tenants WHERE org_id = ANY($1::text[])", orgs_list)


def _approved_spend(org: str, amount: int, department: str = _DEPARTMENT) -> DecisionResult:
    """An APPROVED result carrying a capital ask of *amount* minor units — exactly what
    the pipeline hands the publisher once every stage has passed."""
    decision_id = str(uuid.uuid4())
    step = EvaluationStepRecord(
        stage=EvaluationStage.AUTHORITY,
        passed=True,
        outcome=None,
        detail={
            "department": department,
            "action_kind": "sales.campaign_proposed",
            "partition_key": f"campaign:{decision_id[:8]}",
            "correlation_id": str(uuid.uuid4()),
        },
        duration_ms=1.0,
        timestamp=datetime.now(timezone.utc),
    )
    return DecisionResult(
        decision_id=decision_id,
        event_id=str(uuid.uuid4()),
        tenant_id=org,
        outcome=DecisionOutcome.APPROVED,
        scoring=None,
        capital=CapitalCheckResult(
            available_budget=Decimal(10_000),
            requested_amount=Decimal(amount),
            ceiling_pct=0.0,
            passes=True,
            reason="stage-4 pre-check passed",
        ),
        final_reason="confirmed APPROVED (risk_band=LOW)",
        steps=[step],
        evaluated_at=datetime.now(timezone.utc),
    )


async def _outcomes_by_decision(admin_conn, org: str) -> dict[str, str]:
    rows = await admin_conn.fetch(
        "SELECT decision_id, outcome FROM decisions WHERE org_id=$1", org
    )
    return {str(r["decision_id"]): r["outcome"] for r in rows}


# ---------------------------------------------------------------------------
# PROPERTY 1 — concurrency: N proposals never jointly overshoot; losers defer.
# This is the property that FAILS against the pre-fix code (no reservation ⇒ every
# proposal approves and committed never moves off 0).
# ---------------------------------------------------------------------------

@requires_app_role
@pytest.mark.asyncio
async def test_concurrent_reservations_never_overshoot_and_losers_defer(app_db, admin_conn):
    org = f"org_cap_{uuid.uuid4().hex[:8]}"
    await _seed_tenant(admin_conn, org)
    # ceiling 1000, single department ⇒ total_org 1000, reserve_floor = 1000*0.15 = 150,
    # spendable = 850. Each proposal asks 170 ⇒ exactly 5 fit (5*170 = 850), the rest
    # must defer. 8 fired concurrently ⇒ 5 approved, 3 deferred.
    await _seed_ledger(admin_conn, org, ceiling=1000, committed=0)
    try:
        publisher = DecisionEventPublisher(db=app_db, settings=_settings())
        proposals = [_approved_spend(org, 170) for _ in range(8)]

        # Fire all eight against the one ceiling at once. Assertions read the DURABLE
        # state, not the return values, so this test states the pure overshoot property
        # — which the pre-fix publisher (no reservation) violates: every proposal
        # approves and committed never moves off 0.
        await asyncio.gather(*(publisher.publish_outcome(p) for p in proposals))

        committed = await _committed(admin_conn, org)
        persisted = await _outcomes_by_decision(admin_conn, org)
        approved = sum(v == "approved" for v in persisted.values())
        deferred = sum(v == "deferred_to_human" for v in persisted.values())

        # THE invariant: committed never exceeds spendable (let alone the ceiling).
        assert committed <= 850, f"overshoot: committed={committed} > spendable 850"
        assert committed <= 1000
        # Exactly the winners reserved; nothing partial, nothing double-counted.
        assert committed == approved * 170
        assert approved == 5, f"expected 5 approved within the ceiling, got {approved}"
        # Losers DEFER — they are never approved (the pre-fix failure mode is 8 approved).
        assert deferred == 3, f"expected 3 deferred (SPEND_OVER_CEILING), got {deferred}"
        assert approved + deferred == 8
    finally:
        await _cleanup(admin_conn, org)


# ---------------------------------------------------------------------------
# PROPERTY 2 — a reservation that would breach converts to DEFERRED, never approves.
# ---------------------------------------------------------------------------

@requires_app_role
@pytest.mark.asyncio
async def test_reservation_failure_converts_to_deferred(app_db, admin_conn):
    org = f"org_cap_{uuid.uuid4().hex[:8]}"
    await _seed_tenant(admin_conn, org)
    # spendable = (1000 - 900) - 150 = -50 < any positive ask ⇒ every spend must defer.
    await _seed_ledger(admin_conn, org, ceiling=1000, committed=900)
    try:
        publisher = DecisionEventPublisher(db=app_db, settings=_settings())
        result = _approved_spend(org, 10)

        effective = await publisher.publish_outcome(result)

        assert effective.outcome is DecisionOutcome.DEFERRED_TO_HUMAN
        assert "SPEND_OVER_CEILING" in effective.final_reason
        # Nothing was committed — the proposal was over the ceiling, not partially taken.
        assert await _committed(admin_conn, org) == 900
        persisted = await _outcomes_by_decision(admin_conn, org)
        assert persisted[result.decision_id] == "deferred_to_human"
    finally:
        await _cleanup(admin_conn, org)


# ---------------------------------------------------------------------------
# PROPERTY 3 — the reservation and the decisions row commit or roll back TOGETHER.
# Kill the transaction after the reservation UPDATE but before the CTE commits and
# assert NEITHER landed.
# ---------------------------------------------------------------------------

@requires_app_role
@pytest.mark.asyncio
async def test_reservation_and_decision_are_atomic(app_db, admin_conn):
    org = f"org_cap_{uuid.uuid4().hex[:8]}"
    await _seed_tenant(admin_conn, org)
    await _seed_ledger(admin_conn, org, ceiling=1000, committed=0)
    try:
        publisher = DecisionEventPublisher(db=app_db, settings=_settings())
        result = _approved_spend(org, 200)

        # Inject a failure AFTER reserve_committed has incremented committed in the
        # transaction, but as the decisions/outbox write is attempted.
        async def _boom(*_args, **_kwargs):
            raise RuntimeError("injected mid-transaction failure")

        publisher._write_decision_and_outbox = _boom  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="injected"):
            await publisher.publish_outcome(result)

        # The reservation rolled back with the (never-written) decisions row.
        assert await _committed(admin_conn, org) == 0, "reservation leaked after rollback"
        persisted = await _outcomes_by_decision(admin_conn, org)
        assert result.decision_id not in persisted, "decisions row landed despite rollback"
    finally:
        await _cleanup(admin_conn, org)


# ---------------------------------------------------------------------------
# PROPERTY 4 — RLS: tenant A cannot reserve against tenant B's ledger.
# ---------------------------------------------------------------------------

@requires_app_role
@pytest.mark.asyncio
async def test_reservation_rls_isolation(app_db, admin_conn):
    suffix = uuid.uuid4().hex[:8]
    org_a, org_b = f"org_a_{suffix}", f"org_b_{suffix}"
    await _seed_tenant(admin_conn, org_a)
    await _seed_tenant(admin_conn, org_b)
    # Only B has a ledger row, with ample headroom.
    await _seed_ledger(admin_conn, org_b, ceiling=1000, committed=100)
    try:
        capital = CapitalDAL(app_db, _settings())

        # Under tenant A's session, B's ledger row is invisible (RLS), so the
        # reservation finds no row and fails closed — it can NEVER touch B's committed.
        async with app_db.tenant_session(org_a) as conn:
            reserved = await capital.reserve_committed(conn, _DEPARTMENT, Decimal(50))
        assert reserved is False, "RLS leak: tenant A reserved against a foreign ledger"

        assert await _committed(admin_conn, org_b) == 100, "RLS leak: B's committed moved"

        # Sanity: B reserving against its OWN ledger succeeds (RLS is not just denying all).
        async with app_db.tenant_session(org_b) as conn:
            reserved_b = await capital.reserve_committed(conn, _DEPARTMENT, Decimal(50))
        assert reserved_b is True
        assert await _committed(admin_conn, org_b) == 150
    finally:
        await _cleanup(admin_conn, org_a, org_b)


# ---------------------------------------------------------------------------
# PROPERTY 5 — idempotency: a redelivered decision reserves exactly ONCE.
# (The consumer's ProcessedEventStore leaves a crash window open; the decision_id
# guard inside the reserving path closes it for the money write.)
# ---------------------------------------------------------------------------

@requires_app_role
@pytest.mark.asyncio
async def test_reservation_idempotent_across_redelivery(app_db, admin_conn):
    org = f"org_cap_{uuid.uuid4().hex[:8]}"
    await _seed_tenant(admin_conn, org)
    await _seed_ledger(admin_conn, org, ceiling=1000, committed=0)
    try:
        publisher = DecisionEventPublisher(db=app_db, settings=_settings())
        result = _approved_spend(org, 200)

        first = await publisher.publish_outcome(result)
        second = await publisher.publish_outcome(result)  # redelivery: same decision_id

        assert first.outcome is DecisionOutcome.APPROVED
        assert second.outcome is DecisionOutcome.APPROVED
        # Reserved once, not twice.
        assert await _committed(admin_conn, org) == 200
        rows = await admin_conn.fetchval(
            "SELECT COUNT(*) FROM decisions WHERE org_id=$1 AND decision_id=$2",
            org, uuid.UUID(result.decision_id),
        )
        assert rows == 1
    finally:
        await _cleanup(admin_conn, org)


# ---------------------------------------------------------------------------
# PROPERTY 6 — release reverses a reservation (the primitive the settlement /
# compensation consumer will call; see DECISIONS_PENDING.md for the wiring gap).
# ---------------------------------------------------------------------------

@requires_app_role
@pytest.mark.asyncio
async def test_release_reverses_reservation(app_db, admin_conn):
    org = f"org_cap_{uuid.uuid4().hex[:8]}"
    await _seed_tenant(admin_conn, org)
    await _seed_ledger(admin_conn, org, ceiling=1000, committed=300)
    try:
        capital = CapitalDAL(app_db, _settings())

        async with app_db.tenant_session(org) as conn:
            new_committed = await capital.release_committed(conn, _DEPARTMENT, Decimal(100))
        assert new_committed == Decimal(200)
        assert await _committed(admin_conn, org) == 200

        # Over-release floors at spent (0 here), never negative — the spent<=committed
        # invariant is preserved.
        async with app_db.tenant_session(org) as conn:
            floored = await capital.release_committed(conn, _DEPARTMENT, Decimal(9_999))
        assert floored == Decimal(0)
        assert await _committed(admin_conn, org) == 0
    finally:
        await _cleanup(admin_conn, org)
