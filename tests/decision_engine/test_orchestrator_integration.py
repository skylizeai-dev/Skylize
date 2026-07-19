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

import json
import uuid
from datetime import datetime, timezone

from skylize.decision_engine.hitl_writer import HITLQueueWriter
from skylize.decision_engine.models import DecisionContext, DecisionOutcome, OPAResult
from skylize.decision_engine.orchestrator import DecisionOrchestrator
from skylize.decision_engine.pipeline import EvaluationPipeline
from skylize.decision_engine.publisher import DecisionEventPublisher
from skylize.decision_engine.scoring import ScoringEngine


class _MockOPA:
    """Stand-in for OPAClient: canned allow/deny, no live server, no HTTP."""

    def __init__(
        self,
        allow: bool = True,
        deny_reasons: list[str] | None = None,
        require_human: bool = False,
    ) -> None:
        self._allow = allow
        self._deny = deny_reasons or []
        self._require_human = require_human
        self.calls: list[str] = []

    async def evaluate(self, context, scoring_result=None):
        self.calls.append(context.event_id)
        return OPAResult(
            allow=self._allow,
            require_human=self._require_human,
            deny_reasons=list(self._deny),
            policy_version="test",
        )


class _NoCapital:
    """CapitalDAL stand-in: no spend requested, so the CAPITAL stage auto-passes."""

    async def extract_requested_amount(self, context):
        return None


def _context(payload: dict) -> DecisionContext:
    return DecisionContext(
        event_id=str(uuid.uuid4()),
        tenant_id="org_test",
        # ADR-0005: campaign proposals are produced by `director_growth`, whose
        # contract is department="growth". The previous department="sales" pairing
        # was unreachable in production — `sales` is the SDR channel — and only
        # went green because this fixture constructs the context directly,
        # bypassing the transport.
        department="growth",
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


def _outbox_payload_hitl_id(fake_conn) -> str:
    """hitl_id embedded in the decision.deferred_to_human outbox event payload."""
    for call in fake_conn.execute.call_args_list:
        sql = call.args[0]
        if "INSERT INTO decision_outbox" in sql:
            payload = json.loads(call.args[19])  # $19 = payload::jsonb
            return payload["payload"]["hitl_id"]
    raise AssertionError("no decision_outbox insert found")


def _hitl_queue_row_hitl_id(fake_conn) -> str:
    """hitl_id inserted as the ``hitl_queue.hitl_id`` column."""
    for call in fake_conn.execute.call_args_list:
        sql = call.args[0]
        if "INSERT INTO hitl_queue" in sql:
            return str(call.args[1])  # $1 = hitl_id
    raise AssertionError("no hitl_queue insert found")


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
# OPA require_human DEFER path: policy flags for human review → DEFERRED_TO_HUMAN
# even though allow=True (require_human takes precedence, guardrails.md §5)
# ---------------------------------------------------------------------------

async def test_opa_require_human_defers_writes_decision_outbox_and_hitl(
    settings, mock_db, mock_redis, fake_conn
):
    opa = _MockOPA(allow=True, require_human=True)
    orch = _build(settings, mock_db, mock_redis, opa)
    ctx = _context({"campaign_id": "c4"})

    result = await orch.process(ctx)

    assert result.outcome is DecisionOutcome.DEFERRED_TO_HUMAN
    assert opa.calls == [ctx.event_id]

    sqls = _executed_sql(fake_conn)
    assert any(
        "INSERT INTO decisions" in s and "INSERT INTO decision_outbox" in s
        for s in sqls
    )
    assert any("INSERT INTO hitl_queue" in s for s in sqls)

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


# ---------------------------------------------------------------------------
# HARD GATE: the decision.deferred_to_human event payload and the hitl_queue
# row agree on hitl_id — a single id minted once by the orchestrator and
# threaded through both writers (fixes the publisher.py/hitl_writer.py seam
# where each mints its own uuid4() independently).
# ---------------------------------------------------------------------------

async def test_hitl_id_agrees_between_event_and_queue_row(
    settings, mock_db, mock_redis, fake_conn
):
    opa = _MockOPA(allow=True)
    orch = _build(settings, mock_db, mock_redis, opa)
    ctx = _context({"approve": True, "reject": True, "campaign_id": "c1"})

    result = await orch.process(ctx)
    assert result.outcome is DecisionOutcome.DEFERRED_TO_HUMAN

    event_hitl_id = _outbox_payload_hitl_id(fake_conn)
    queue_hitl_id = _hitl_queue_row_hitl_id(fake_conn)
    assert event_hitl_id == queue_hitl_id

    # The governance stream event carries the same id too.
    mock_redis.xadd.assert_awaited()
    assert mock_redis.xadd.await_args.args[1]["hitl_queue_id"] == queue_hitl_id


# ---------------------------------------------------------------------------
# HARD GATE: redelivery of the same proposal reconstructs the same hitl_id
# (deterministic uuid5 derivation) instead of minting a duplicate ticket.
# ---------------------------------------------------------------------------

async def test_hitl_id_stable_across_redelivery(
    settings, mock_db, mock_redis, fake_conn
):
    """Same event_id → same decision_id → same hitl_id, end to end.

    Redelivery of an at-least-once event reconstructs the identical hitl_id
    (via the deterministic uuid5 derivation) rather than minting a new ticket;
    ``check_duplicate_escalation`` then dedupes the second write, matching how
    the real consumer handles a redelivered proposal.
    """
    from skylize.decision_engine.pipeline import decision_id_for, hitl_id_for

    opa = _MockOPA(allow=True)
    orch = _build(settings, mock_db, mock_redis, opa)
    event_id = str(uuid.uuid4())
    ctx = _context({"approve": True, "reject": True, "campaign_id": "c1"}).model_copy(
        update={"event_id": event_id}
    )

    first = await orch.process(ctx)
    first_event_hitl_id = _outbox_payload_hitl_id(fake_conn)
    first_queue_hitl_id = _hitl_queue_row_hitl_id(fake_conn)
    assert first_event_hitl_id == first_queue_hitl_id

    expected_hitl_id = str(hitl_id_for(decision_id_for(event_id)))
    assert first_queue_hitl_id == expected_hitl_id

    # Simulate redelivery: the hitl_queue row from the first attempt is now
    # visible as a pending duplicate.
    fake_conn.execute.reset_mock()
    fake_conn.fetchrow.return_value = {"hitl_id": first_queue_hitl_id}

    second = await orch.process(ctx)

    assert second.decision_id == first.decision_id == decision_id_for(event_id)
    # The escalation write is deduplicated (no second hitl_queue insert)...
    sqls_after_redelivery = _executed_sql(fake_conn)
    assert not any("INSERT INTO hitl_queue" in s for s in sqls_after_redelivery)
    # ...but the redelivered event still carries the SAME hitl_id it did the
    # first time — the deterministic derivation, not a fresh mint.
    assert _outbox_payload_hitl_id(fake_conn) == first_queue_hitl_id
