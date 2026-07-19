"""HITL resume — a human verdict drives a deferred decision to terminal state.

The gates these tests exist to hold (all of them are properties a regression
would silently break, not incidental behaviour):

  1. The resume path NEVER runs the six-stage pipeline. A human already ruled;
     re-evaluating would let policy overturn them.
  2. The SAME deterministic `hitl_id` correlates the escalation and the resume.
  3. A redelivered approval does not double-terminate.
  4. `governance` in the vocabulary table drives BOTH subscription and the
     AUTHORITY exclusion (asserted in test_department_vocabulary.py).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest

from skylize.dal.memory import InMemoryProcessedEventStore
from skylize.decision_engine.constants import SUBSCRIBED_DEPARTMENTS
from skylize.decision_engine.consumer import DecisionEngineConsumer
from skylize.decision_engine.models import DecisionContext, DecisionOutcome
from skylize.decision_engine.pipeline import decision_id_for, hitl_id_for
from skylize.decision_engine.resume import HITLResumeHandler, resume_dedup_key
from skylize.events.bus import DeliveredEvent
from skylize.schemas.base import BaseEvent
from skylize.schemas.events.governance import GovernanceHumanApprovalReceived

from .conftest import make_decision_result

ORG = "org_test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verdict(
    *,
    decision_id: uuid.UUID,
    hitl_id: uuid.UUID,
    approved: bool = True,
    decided_by: str = "owner@skylize.test",
    reason: str | None = None,
) -> GovernanceHumanApprovalReceived:
    """A human verdict, stamped the way the console publishes it."""
    return GovernanceHumanApprovalReceived(
        tenant_id=ORG,
        partition_key=str(decision_id),
        department="governance",
        correlation_id=uuid.uuid4(),
        payload=GovernanceHumanApprovalReceived.Payload(
            decision_id=decision_id,
            hitl_id=hitl_id,
            approved=approved,
            decided_by=decided_by,
            reason=reason,
        ),
    )


class _ResolvingConn:
    """asyncpg conn stub whose `fetchval` models the hitl_queue status guard.

    The real idempotency lives in `WHERE status = 'pending'`: the first resume
    updates the row and the CTE chain enqueues an outbox row; a redelivery
    matches nothing and the INSERT ... SELECT yields no row, so `fetchval`
    returns None. This stub reproduces exactly that, keyed on hitl_id.
    """

    def __init__(self, pending: set[str]) -> None:
        self._pending = pending
        self.calls: list[tuple[str, tuple]] = []

    async def fetchval(self, sql: str, *args):
        self.calls.append((sql, args))
        hitl_id = str(args[4])  # $5
        if hitl_id not in self._pending:
            return None  # already terminal — no rows updated, nothing enqueued
        self._pending.discard(hitl_id)
        return uuid.uuid4()  # decision_outbox.id

    async def execute(self, sql: str, *args):  # pragma: no cover - unused here
        self.calls.append((sql, args))


def _db_with(conn) -> object:
    from contextlib import asynccontextmanager
    from unittest.mock import MagicMock

    db = MagicMock()

    @asynccontextmanager
    async def _tenant_session(tenant_id: str):
        yield conn

    db.tenant_session = _tenant_session
    return db


class _ParkedBus:
    """EventBus double that records subscriptions and never yields."""

    def __init__(self) -> None:
        self.subscriptions: list[tuple[str, str]] = []

    async def publish(self, event: BaseEvent) -> str:  # pragma: no cover - unused
        return "1-0"

    async def consume(
        self, *, tenant_id: str, department: str, group: str, consumer: str
    ) -> AsyncIterator[DeliveredEvent]:
        self.subscriptions.append((tenant_id, department))
        await asyncio.Event().wait()
        yield  # pragma: no cover - unreachable

    async def ack(self, delivered: DeliveredEvent, *, group: str) -> None: ...

    async def to_dlq(self, delivered: DeliveredEvent, *, reason: str) -> None: ...


def _pipeline_spy():
    """A pipeline_fn that records every context it is asked to evaluate."""
    seen: list[DecisionContext] = []

    async def _fn(context: DecisionContext):
        seen.append(context)
        return make_decision_result(
            outcome=DecisionOutcome.APPROVED, event_id=context.event_id
        )

    return _fn, seen


# ---------------------------------------------------------------------------
# GATE 1: a resume never runs the pipeline
# ---------------------------------------------------------------------------


async def test_resume_event_does_not_run_the_six_stages(settings):
    """The load-bearing assertion: pipeline_fn is never called for a verdict."""
    pipeline_fn, seen = _pipeline_spy()
    resumed: list[GovernanceHumanApprovalReceived] = []

    async def _resume(event):
        resumed.append(event)
        return True

    consumer = DecisionEngineConsumer(
        _ParkedBus(), settings, pipeline_fn, resume_fn=_resume
    )
    decision_id = uuid.uuid4()
    event = _verdict(decision_id=decision_id, hitl_id=hitl_id_for(str(decision_id)))

    await consumer._handle_event(event)

    assert seen == [], "a human verdict must never reach the evaluation pipeline"
    assert len(resumed) == 1
    assert resumed[0].payload.decision_id == decision_id


async def test_consumer_raises_when_a_verdict_arrives_with_no_resume_handler(settings):
    """Dropping a human's verdict silently would strand the decision forever."""
    pipeline_fn, seen = _pipeline_spy()
    consumer = DecisionEngineConsumer(_ParkedBus(), settings, pipeline_fn)
    decision_id = uuid.uuid4()

    with pytest.raises(RuntimeError, match="no resume_fn is wired"):
        await consumer._handle_event(
            _verdict(decision_id=decision_id, hitl_id=hitl_id_for(str(decision_id)))
        )

    assert seen == [], "the verdict must not fall through to the pipeline either"


# ---------------------------------------------------------------------------
# GATE 2: the same hitl_id correlates escalation and resume
# ---------------------------------------------------------------------------


def test_hitl_id_is_deterministic_from_the_decision_id():
    """The correlation key is minted once and reconstructible, not stored."""
    decision_id = decision_id_for("event-abc")
    assert hitl_id_for(decision_id) == hitl_id_for(decision_id)
    assert hitl_id_for(decision_id) != hitl_id_for(decision_id_for("event-xyz"))


async def test_resume_targets_the_hitl_row_by_the_deterministic_hitl_id(settings):
    """The UPDATE is keyed on the same hitl_id the escalation wrote."""
    decision_id = uuid.uuid4()
    hitl_id = hitl_id_for(str(decision_id))
    conn = _ResolvingConn(pending={str(hitl_id)})
    handler = HITLResumeHandler(_db_with(conn), settings)

    resolved = await handler.resume(
        _verdict(decision_id=decision_id, hitl_id=hitl_id, decided_by="ceo@x.test")
    )

    assert resolved is True
    sql, args = conn.calls[0]
    assert args[4] == hitl_id, "the hitl_id is the correlation key"
    assert args[5] == ORG, "tenant-scoped, so RLS and the predicate agree"
    assert args[0] == "approved"  # hitl_queue.status
    assert args[6] == "approved"  # decisions.outcome
    assert args[1] == "ceo@x.test"  # verdict_by
    assert "status = 'pending'" in sql, "the durable idempotency guard"


# ---------------------------------------------------------------------------
# GATE 3: a redelivered verdict does not double-terminate
# ---------------------------------------------------------------------------


async def test_redelivered_verdict_enqueues_no_second_terminal_event(settings):
    """Second delivery updates zero rows, so the outbox INSERT selects nothing."""
    decision_id = uuid.uuid4()
    hitl_id = hitl_id_for(str(decision_id))
    conn = _ResolvingConn(pending={str(hitl_id)})
    handler = HITLResumeHandler(_db_with(conn), settings)
    event = _verdict(decision_id=decision_id, hitl_id=hitl_id)

    first = await handler.resume(event)
    second = await handler.resume(event)

    assert first is True
    assert second is False, "a redelivery must not terminate the decision twice"
    assert len(conn.calls) == 2, "both were attempted; only one took effect"


async def test_consumer_short_circuits_a_redelivered_verdict(settings):
    """The ProcessedEventStore layer, keyed on hitl_id not the event_id.

    Two publications of the same human decision carry different `event_id`s, so
    keying on `event_id` would not dedup them at all.
    """
    pipeline_fn, _ = _pipeline_spy()
    calls: list[uuid.UUID] = []

    async def _resume(event):
        calls.append(event.payload.hitl_id)
        return True

    processed = InMemoryProcessedEventStore()
    consumer = DecisionEngineConsumer(
        _ParkedBus(), settings, pipeline_fn, resume_fn=_resume, processed=processed
    )
    decision_id = uuid.uuid4()
    hitl_id = hitl_id_for(str(decision_id))

    # Same verdict, republished — distinct event_id, identical hitl_id.
    await consumer._handle_event(_verdict(decision_id=decision_id, hitl_id=hitl_id))
    await consumer._handle_event(_verdict(decision_id=decision_id, hitl_id=hitl_id))

    assert calls == [hitl_id], "the second delivery must not reach the handler"
    assert await processed.is_processed(resume_dedup_key(hitl_id), org_id=ORG)


async def test_resume_dedup_key_cannot_collide_with_a_proposal_event_id():
    """Both live in one ProcessedEventStore; the namespace prefix keeps them apart."""
    shared = uuid.uuid4()
    assert resume_dedup_key(shared) != str(shared)
    assert resume_dedup_key(shared).startswith("hitl:")


# ---------------------------------------------------------------------------
# GATE 4: the terminal event is a real, validated decision.* event
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("approved", "event_type", "outcome"),
    [(True, "decision.approved", "approved"), (False, "decision.rejected", "rejected")],
)
async def test_resume_enqueues_the_terminal_decision_event(
    settings, approved, event_type, outcome
):
    decision_id = uuid.uuid4()
    hitl_id = hitl_id_for(str(decision_id))
    conn = _ResolvingConn(pending={str(hitl_id)})
    handler = HITLResumeHandler(_db_with(conn), settings)

    await handler.resume(
        _verdict(
            decision_id=decision_id,
            hitl_id=hitl_id,
            approved=approved,
            reason="not this quarter",
        )
    )

    _sql, args = conn.calls[0]
    assert args[10] == event_type                        # $11 outbox event_type
    assert args[9] == f"evt:{ORG}:decision"              # $10 the canonical channel
    assert args[6] == outcome                            # $7 decisions.outcome

    payload = json.loads(args[11])                       # $12 outbound payload
    assert payload["type"] == event_type
    assert payload["payload"]["decision_id"] == str(decision_id)
    assert payload["payload"]["action_kind"] == "human_resumed"
    if approved:
        assert payload["payload"]["approved_scope"]["hitl_id"] == str(hitl_id)
    else:
        assert payload["payload"]["reasons"] == ["not this quarter"]


async def test_rejection_without_a_reason_still_produces_a_reason(settings):
    """decision.rejected requires reasons; a terse human verdict must not fail it."""
    decision_id = uuid.uuid4()
    hitl_id = hitl_id_for(str(decision_id))
    conn = _ResolvingConn(pending={str(hitl_id)})
    handler = HITLResumeHandler(_db_with(conn), settings)

    await handler.resume(
        _verdict(decision_id=decision_id, hitl_id=hitl_id, approved=False, reason=None)
    )

    payload = json.loads(conn.calls[0][1][11])
    assert payload["payload"]["reasons"] == ["human_rejected"]


async def test_verdict_json_records_who_decided_and_why(settings):
    """The hitl_queue row keeps the audit trail, not just the status flip."""
    decision_id = uuid.uuid4()
    hitl_id = hitl_id_for(str(decision_id))
    conn = _ResolvingConn(pending={str(hitl_id)})
    handler = HITLResumeHandler(_db_with(conn), settings)
    event = _verdict(
        decision_id=decision_id,
        hitl_id=hitl_id,
        approved=False,
        decided_by="owner@x.test",
        reason="budget freeze",
    )

    await handler.resume(event)

    verdict = json.loads(conn.calls[0][1][2])  # $3 verdict_json
    assert verdict == {
        "approved": False,
        "decided_by": "owner@x.test",
        "reason": "budget freeze",
        "resume_event_id": str(event.event_id),
    }


async def test_verdict_for_an_unknown_hitl_id_is_a_noop_not_a_raise(settings):
    """A verdict with no pending row must not retry forever in the router."""
    decision_id = uuid.uuid4()
    conn = _ResolvingConn(pending=set())
    handler = HITLResumeHandler(_db_with(conn), settings)

    assert await handler.resume(
        _verdict(decision_id=decision_id, hitl_id=uuid.uuid4())
    ) is False


# ---------------------------------------------------------------------------
# GATE 5: the governance channel is actually subscribed
# ---------------------------------------------------------------------------


async def test_full_cycle_defer_then_resume_shares_one_hitl_id(
    settings, mock_db, mock_redis, fake_conn
):
    """THE end-to-end gate, both halves joined by the one correlation key.

        proposal → DEFERRED_TO_HUMAN → hitl_queue row + governance escalation
                 → human verdict published → terminal decision.* enqueued

    Asserts the SAME hitl_id appears in all four places. If the deferral and the
    resume ever minted it independently the chain would break silently: the
    verdict would update zero rows and the decision would sit `pending` forever
    while every component looked healthy.
    """
    from skylize.decision_engine.hitl_writer import HITLQueueWriter
    from skylize.decision_engine.orchestrator import DecisionOrchestrator
    from skylize.decision_engine.pipeline import EvaluationPipeline
    from skylize.decision_engine.publisher import DecisionEventPublisher
    from skylize.decision_engine.scoring import ScoringEngine

    from .test_orchestrator_integration import _MockOPA, _NoCapital

    # --- half 1: a proposal is deferred to a human -------------------------
    orchestrator = DecisionOrchestrator(
        EvaluationPipeline(
            opa_client=_MockOPA(allow=True),
            scoring_engine=ScoringEngine(settings),
            capital_dal=_NoCapital(),
            settings=settings,
            event_bus=None,
        ),
        DecisionEventPublisher(db=mock_db, settings=settings),
        HITLQueueWriter(db=mock_db, redis=mock_redis, settings=settings),
    )
    context = DecisionContext(
        event_id=str(uuid.uuid4()),
        tenant_id=ORG,
        department="growth",
        event_type="sales.campaign_proposed",
        # Contradictory signals → the CONFLICT stage defers to a human.
        payload={"approve": True, "reject": True, "campaign_id": "c1"},
        received_at=datetime.now(timezone.utc),
    )

    deferred = await orchestrator.process(context)
    assert deferred.outcome is DecisionOutcome.DEFERRED_TO_HUMAN

    # The hitl_queue row the escalation wrote, and the id in the event payload.
    row_hitl_id = None
    event_hitl_id = None
    for call in fake_conn.execute.call_args_list:
        sql = call.args[0]
        if "INSERT INTO hitl_queue" in sql:
            row_hitl_id = str(call.args[1])
        elif "INSERT INTO decision_outbox" in sql:
            event_hitl_id = json.loads(call.args[19])["payload"]["hitl_id"]

    expected = str(hitl_id_for(deferred.decision_id))
    assert row_hitl_id == expected, "hitl_queue row must carry the minted id"
    assert event_hitl_id == expected, "the deferral event must carry the same id"
    mock_redis.xadd.assert_awaited()
    assert mock_redis.xadd.await_args.args[0] == f"evt:{ORG}:governance"

    # --- half 2: a human answers, on that same id --------------------------
    resume_conn = _ResolvingConn(pending={expected})
    handler = HITLResumeHandler(_db_with(resume_conn), settings)
    consumer = DecisionEngineConsumer(
        _ParkedBus(),
        settings,
        _pipeline_spy()[0],
        resume_fn=handler.resume,
        processed=InMemoryProcessedEventStore(),
    )

    verdict = _verdict(
        decision_id=uuid.UUID(deferred.decision_id),
        hitl_id=hitl_id_for(deferred.decision_id),
        approved=True,
        decided_by="owner@skylize.test",
    )
    await consumer._handle_event(verdict)

    assert resume_conn.calls, "the verdict must have reached the durable handler"
    _sql, args = resume_conn.calls[0]
    assert str(args[4]) == expected, "the resume must target the SAME hitl_id"
    assert args[10] == "decision.approved"
    assert json.loads(args[11])["payload"]["decision_id"] == deferred.decision_id

    # --- and a redelivery of that verdict changes nothing -------------------
    await consumer._handle_event(verdict)
    assert len(resume_conn.calls) == 1, "redelivery must not re-terminate"


async def test_consumer_subscribes_to_the_governance_channel(settings):
    """Without this subscription a verdict never arrives at all."""
    bus = _ParkedBus()
    pipeline_fn, _ = _pipeline_spy()

    async def _resume(event):  # pragma: no cover - not exercised here
        return True

    consumer = DecisionEngineConsumer(bus, settings, pipeline_fn, resume_fn=_resume)
    consumer.subscribe(ORG)
    for _ in range(200):
        if len(bus.subscriptions) == len(SUBSCRIBED_DEPARTMENTS):
            break
        await asyncio.sleep(0)
    await consumer.stop()

    assert (ORG, "governance") in bus.subscriptions
