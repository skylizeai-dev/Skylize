"""DecisionEngine: event consumption, emission, idempotency, HITL resume.

Uses the in-memory bus + repos so the full consume → decide → emit → audit path
runs with no infrastructure."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from skylize.app.audit.service import AuditService
from skylize.app.decision_engine import DecisionEngine
from skylize.app.decision_engine.events import decision_id_for, hitl_id_for
from skylize.config import Settings
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.memory import (
    InMemoryAuditRepository,
    InMemoryCapitalRepository,
    InMemoryProcessedEventStore,
)
from skylize.dal.ports import BudgetCeiling
from skylize.events.memory_bus import InMemoryEventBus
from skylize.schemas.events.creative import CreativeReviewRequested
from skylize.schemas.events.governance import GovernanceHumanApprovalReceived

ORG = "org_test"


def _engine(bus: InMemoryEventBus, **kw) -> DecisionEngine:
    audit = AuditService(bus, InMemoryAuditRepository())
    return DecisionEngine(bus, MVP_REGISTRY, audit, Settings(backend="memory"), **kw)


def _review_event(
    *, agent: str = "copy_director", action: str = "approve_internal",
    spend: int | None = None, partition: str = "brief:1",
) -> CreativeReviewRequested:
    return CreativeReviewRequested(
        tenant_id=ORG,
        partition_key=partition,
        department="creative",
        source_agent_id=agent,
        correlation_id=uuid4(),
        payload=CreativeReviewRequested.Payload(
            brief_id=uuid4(),
            asset_ids=[uuid4()],
            proposed_action=action,
            proposed_spend_minor_units=spend,
        ),
    )


async def _wait_for(bus: InMemoryEventBus, type_: str, n: int = 1) -> None:
    for _ in range(200):
        if len(bus.published_of_type(type_)) >= n:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"timed out waiting for {n}x {type_}")


# -- emission ---------------------------------------------------------------
async def test_handle_event_approves_and_audits() -> None:
    bus = InMemoryEventBus()
    engine = _engine(bus)
    await engine._handle_event(_review_event())
    assert bus.published_of_type("decision.evaluated")
    assert bus.published_of_type("decision.approved")
    assert bus.published_of_type("audit.action_recorded")


async def test_handle_event_rejects_worker_spend() -> None:
    bus = InMemoryEventBus()
    engine = _engine(bus)
    # A worker proposing spend is denied by policy → decision.rejected.
    await engine._handle_event(
        _review_event(agent="hook_generator_agent", action="approve_internal", spend=500)
    )
    rejected = bus.published_of_type("decision.rejected")
    assert rejected
    assert rejected[0].payload.stage_rejected_at == "opa_policy"


async def test_handle_event_defers_external_launch_by_worker() -> None:
    bus = InMemoryEventBus()
    engine = _engine(bus)
    await engine._handle_event(
        _review_event(agent="hook_generator_agent", action="launch")
    )
    deferred = bus.published_of_type("decision.deferred_to_human")
    assert deferred
    assert deferred[0].payload.decision_id  # carries the decision ticket


# -- idempotency ------------------------------------------------------------
async def test_idempotent_on_event_id() -> None:
    bus = InMemoryEventBus()
    processed = InMemoryProcessedEventStore()
    engine = _engine(bus, processed=processed)
    event = _review_event()
    await engine._handle_event(event)
    await engine._handle_event(event)  # redelivery — must not decide twice
    assert len(bus.published_of_type("decision.approved")) == 1
    assert len(bus.published_of_type("decision.evaluated")) == 1
    assert await processed.is_processed(str(event.event_id), org_id=ORG)


async def test_same_event_yields_same_decision_id() -> None:
    bus = InMemoryEventBus()
    engine = _engine(bus)
    event = _review_event()
    await engine._handle_event(event)
    approved = bus.published_of_type("decision.approved")[0]
    assert approved.payload.decision_id == decision_id_for(event.event_id)


# -- capital path through the engine ---------------------------------------
async def test_within_ceiling_campaign_approved() -> None:
    bus = InMemoryEventBus()
    cap = InMemoryCapitalRepository()
    cap.set_ceiling(BudgetCeiling(ORG, "creative", ceiling_minor_units=10_000, committed_minor_units=0))
    engine = _engine(bus, capital=cap)
    await engine._handle_event(
        _review_event(agent="vp_creative", action="stage", spend=2_000, partition="brief:cap")
    )
    assert bus.published_of_type("decision.approved")


# -- background consumption -------------------------------------------------
async def test_consumes_from_subscribed_stream() -> None:
    bus = InMemoryEventBus()
    engine = _engine(bus)
    await engine.start()
    engine.subscribe(ORG, "creative")
    try:
        await bus.publish(_review_event(partition="brief:sub"))
        await _wait_for(bus, "decision.approved")
    finally:
        await engine.stop()
    assert bus.published_of_type("decision.approved")


class _AckDroppingBus(InMemoryEventBus):
    """Swallows the first ack — a worker that died after deciding, before acking.

    This is the window PEL reclaim newly exposes on the INLINE engine: before
    redelivery existed the message simply stranded, so the second delivery below
    could not happen in production. It can now, which makes this the test that
    the engine's idempotency actually holds under it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.dropped = 0

    async def ack(self, delivered, *, group: str) -> None:
        if self.dropped == 0:
            self.dropped += 1
            return  # entry stays pending → the bus redelivers it
        await super().ack(delivered, group=group)


async def test_redelivery_after_a_lost_ack_does_not_decide_twice() -> None:
    """Inline engine: a redelivered event is short-circuited by ProcessedEventStore."""
    bus = _AckDroppingBus()
    processed = InMemoryProcessedEventStore()
    engine = _engine(bus, processed=processed)
    await engine.start()
    engine.subscribe(ORG, "creative")
    event = _review_event(partition="brief:redeliver")
    try:
        await bus.publish(event)
        await _wait_for(bus, "decision.approved")
        for _ in range(200):
            if bus.dropped:
                break
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.05)  # let the redelivery be re-dispatched
    finally:
        await engine.stop()

    assert bus.dropped == 1, "the ack was never dropped — redelivery not exercised"
    # Delivered twice, decided once.
    assert len(bus.published_of_type("decision.approved")) == 1
    assert len(bus.published_of_type("decision.evaluated")) == 1
    assert await processed.is_processed(str(event.event_id), org_id=ORG)


async def test_redelivered_decision_keeps_the_same_decision_id() -> None:
    """The re-emit window is benign because decision_id is derived, not minted.

    If a crash lands between `_emit` and `mark_processed`, redelivery DOES
    re-emit — but with the identical decision_id, so the duplicate collapses on
    the publisher's ON CONFLICT write rather than creating a second decision.
    This is what keeps at-least-once safe for the inline engine.
    """
    bus = InMemoryEventBus()
    await _engine(bus)._handle_event(_ev := _review_event(partition="brief:stable"))
    first = bus.published_of_type("decision.approved")[0].payload.decision_id

    # The same event redelivered to a FRESH engine (restart: no in-memory dedup).
    bus2 = InMemoryEventBus()
    await _engine(bus2)._handle_event(_ev)
    second = bus2.published_of_type("decision.approved")[0].payload.decision_id

    assert first == second == decision_id_for(_ev.event_id)


# -- HITL resume ------------------------------------------------------------
async def test_human_approval_resumes_to_approved() -> None:
    bus = InMemoryEventBus()
    engine = _engine(bus)
    decision_id = uuid4()
    await engine._handle_event(
        GovernanceHumanApprovalReceived(
            tenant_id=ORG,
            partition_key="brief:hitl",
            department="governance",
            correlation_id=uuid4(),
            payload=GovernanceHumanApprovalReceived.Payload(
                decision_id=decision_id,
                hitl_id=hitl_id_for(decision_id),
                approved=True,
                decided_by="user_owner",
            ),
        )
    )
    approved = bus.published_of_type("decision.approved")
    assert approved and approved[0].payload.decision_id == decision_id


async def test_human_rejection_resumes_to_rejected_and_is_idempotent() -> None:
    bus = InMemoryEventBus()
    processed = InMemoryProcessedEventStore()
    engine = _engine(bus, processed=processed)
    decision_id = uuid4()

    def _verdict() -> GovernanceHumanApprovalReceived:
        return GovernanceHumanApprovalReceived(
            tenant_id=ORG,
            partition_key="brief:hitl2",
            department="governance",
            correlation_id=uuid4(),
            payload=GovernanceHumanApprovalReceived.Payload(
                decision_id=decision_id,
                hitl_id=hitl_id_for(decision_id),
                approved=False,
                decided_by="user_owner",
                reason="off-brand",
            ),
        )

    await engine._handle_event(_verdict())
    await engine._handle_event(_verdict())  # second verdict must be ignored
    assert len(bus.published_of_type("decision.rejected")) == 1
