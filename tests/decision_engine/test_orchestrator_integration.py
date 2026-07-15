"""End-to-end integration test for the decision orchestration wrapper.

Drives one proposal through ``DecisionOrchestrator.process`` with a MOCKED OPA
response and FAKE DB / Redis (conftest ``mock_db`` / ``mock_redis``), asserting
the full glue chain the engine runs per proposal:

    proposal → pipeline.evaluate → publisher (decisions + outbox, one tx)
             → HITL escalation (on defer)

OPA has no live server yet, so the OPA_POLICY stage is exercised against a canned
allow/deny. This is exactly the seam that becomes testable end-to-end once a real
OPA server + Rego policies exist — nothing else in the chain is stubbed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from skylize.decision_engine.hitl_writer import HITLQueueWriter
from skylize.decision_engine.models import DecisionContext, DecisionOutcome
from skylize.decision_engine.orchestrator import DecisionOrchestrator
from skylize.decision_engine.pipeline import EvaluationPipeline
from skylize.decision_engine.publisher import DecisionEventPublisher
from skylize.decision_engine.scoring import ScoringEngine


class _MockOPA:
    """Stand-in for OPAClient: canned allow/deny, no live server, no HTTP."""

    def __init__(self, allow: bool = True, deny_reasons: list[str] | None = None) -> None:
        self._allow = allow
        self._deny = deny_reasons or []
        self.calls: list[str] = []

    async def evaluate(self, context, scoring_result=None):
        self.calls.append(context.event_id)
        return (self._allow, list(self._deny))


class _NoCapital:
    """CapitalDAL stand-in: no spend requested, so the CAPITAL stage auto-passes."""

    async def extract_requested_amount(self, context):
        return None


def _context(payload: dict) -> DecisionContext:
    return DecisionContext(
        event_id=str(uuid.uuid4()),
        tenant_id="org_test",
        department="sales",
        event_type="sales.campaign_proposed",
        payload=payload,
        received_at=datetime.now(timezone.utc),
    )


def _build(settings, mock_db, mock_redis, opa: _MockOPA) -> DecisionOrchestrator:
    pipeline = EvaluationPipeline(
        opa_client=opa,
        scoring_engine=ScoringEngine(settings),
        capital_dal=_NoCapital(),
        settings=settings,
        event_bus=None,
    )
    publisher = DecisionEventPublisher(db=mock_db, settings=settings)
    hitl = HITLQueueWriter(db=mock_db, redis=mock_redis, settings=settings)
    return DecisionOrchestrator(pipeline, publisher, hitl)


def _executed_sql(fake_conn) -> list[str]:
    return [call.args[0] for call in fake_conn.execute.call_args_list]


# ---------------------------------------------------------------------------
# DEFER path: OPA allows, CONFLICT stage defers → decisions + outbox + hitl_queue
# ---------------------------------------------------------------------------

async def test_conflict_defer_writes_decision_outbox_and_hitl(
    settings, mock_db, mock_redis, fake_conn
):
    opa = _MockOPA(allow=True)
    orch = _build(settings, mock_db, mock_redis, opa)
    # An approval AND a rejection signal in one payload is internally
    # contradictory → CONFLICT stage routes to DEFERRED_TO_HUMAN.
    ctx = _context({"approve": True, "reject": True, "campaign_id": "c1"})

    result = await orch.process(ctx)

    assert result.outcome is DecisionOutcome.DEFERRED_TO_HUMAN
    assert opa.calls == [ctx.event_id]  # OPA was consulted (mocked)

    sqls = _executed_sql(fake_conn)
    # Publisher CTE (decisions + outbox) AND the hitl_queue insert both ran.
    assert any(
        "INSERT INTO decisions" in s and "INSERT INTO decision_outbox" in s
        for s in sqls
    )
    assert any("INSERT INTO hitl_queue" in s for s in sqls)

    # The escalation event went to the tenant governance stream.
    mock_redis.xadd.assert_awaited()
    assert mock_redis.xadd.await_args.args[0] == "evt:org_test:governance"


# ---------------------------------------------------------------------------
# APPROVE path: OPA allows, benign payload → APPROVED, no hitl_queue write
# ---------------------------------------------------------------------------

async def test_approved_writes_decision_no_hitl(
    settings, mock_db, mock_redis, fake_conn
):
    opa = _MockOPA(allow=True)
    orch = _build(settings, mock_db, mock_redis, opa)
    ctx = _context({"campaign_id": "c2"})

    result = await orch.process(ctx)

    assert result.outcome is DecisionOutcome.APPROVED
    sqls = _executed_sql(fake_conn)
    assert any("INSERT INTO decision_outbox" in s for s in sqls)
    assert not any("INSERT INTO hitl_queue" in s for s in sqls)
    mock_redis.xadd.assert_not_awaited()


# ---------------------------------------------------------------------------
# OPA denies → REJECTED (fail-closed policy path), still persisted, no HITL
# ---------------------------------------------------------------------------

async def test_opa_denial_rejects_and_persists(
    settings, mock_db, mock_redis, fake_conn
):
    opa = _MockOPA(allow=False, deny_reasons=["policy: over budget ceiling"])
    orch = _build(settings, mock_db, mock_redis, opa)
    ctx = _context({"campaign_id": "c3"})

    result = await orch.process(ctx)

    assert result.outcome is DecisionOutcome.REJECTED
    sqls = _executed_sql(fake_conn)
    assert any("INSERT INTO decision_outbox" in s for s in sqls)
    assert not any("INSERT INTO hitl_queue" in s for s in sqls)
    mock_redis.xadd.assert_not_awaited()
