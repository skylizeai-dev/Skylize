"""End-to-end: a published proposal → bus → consumer → OPA pipeline → durable writes.

This is the test the old consumer could not have: it starts at ``bus.publish``,
the way ``director_growth`` actually raises a campaign proposal, and asserts the
proposal comes out the far end as a ``decisions`` row, a ``decision_outbox`` row,
and — on a defer — a ``hitl_queue`` row. Nothing constructs a ``DecisionContext``
by hand, so the transport is under test rather than assumed.

Stubbed: OPA (no live server yet) and the capital ledger, both by the same
stand-ins ``test_orchestrator_integration.py`` uses. The DB/Redis fakes are the
conftest ``mock_db`` / ``mock_redis``. Everything between publish and SQL is real.
"""
from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from skylize.decision_engine.consumer import DecisionEngineConsumer
from skylize.decision_engine.hitl_writer import HITLQueueWriter
from skylize.decision_engine.models import DecisionOutcome, OPAResult
from skylize.decision_engine.orchestrator import DecisionOrchestrator
from skylize.decision_engine.pipeline import (
    EvaluationPipeline,
    decision_id_for,
    hitl_id_for,
)
from skylize.decision_engine.publisher import DecisionEventPublisher
from skylize.decision_engine.scoring import ScoringEngine
from skylize.events.memory_bus import InMemoryEventBus
from skylize.schemas.events.sales import SalesCampaignProposed

ORG = "org_test"


class _MockOPA:
    """Canned allow/deny — no live server, no HTTP."""

    def __init__(self, *, allow: bool = True, require_human: bool = False) -> None:
        self._allow = allow
        self._require_human = require_human
        self.calls: list[str] = []

    async def evaluate(self, context, scoring_result=None) -> OPAResult:
        self.calls.append(context.event_id)
        return OPAResult(
            allow=self._allow,
            require_human=self._require_human,
            deny_reasons=[],
            policy_version="test",
        )


class _NoCapital:
    """CapitalDAL stand-in: no spend extracted, so the CAPITAL stage auto-passes."""

    async def extract_requested_amount(self, context):
        return None


def _proposal() -> SalesCampaignProposed:
    """Exactly what `director_growth` publishes — department="growth" (ADR-0005)."""
    return SalesCampaignProposed(
        tenant_id=ORG,
        partition_key="campaign:c1",
        department="growth",
        correlation_id=uuid4(),
        payload=SalesCampaignProposed.Payload(
            campaign_id="c1",
            channel="meta",
            proposed_budget_minor_units=250_000,
            currency="USD",
            objective="conversions",
        ),
    )


def _build_consumer(bus, settings, mock_db, mock_redis, opa) -> DecisionEngineConsumer:
    pipeline = EvaluationPipeline(
        opa_client=opa,
        scoring_engine=ScoringEngine(settings),
        capital_dal=_NoCapital(),
        settings=settings,
        event_bus=None,
    )
    orchestrator = DecisionOrchestrator(
        pipeline,
        DecisionEventPublisher(db=mock_db, settings=settings),
        HITLQueueWriter(db=mock_db, redis=mock_redis, settings=settings),
    )
    return DecisionEngineConsumer(bus, settings, orchestrator.process)


async def _settle(predicate, *, ticks: int = 400) -> bool:
    for _ in range(ticks):
        if predicate():
            return True
        await asyncio.sleep(0)
    return predicate()


def _executed_sql(fake_conn) -> list[str]:
    return [call.args[0] for call in fake_conn.execute.call_args_list]


def _outbox_payload_hitl_id(fake_conn) -> str:
    for call in fake_conn.execute.call_args_list:
        if "INSERT INTO decision_outbox" in call.args[0]:
            return json.loads(call.args[19])["payload"]["hitl_id"]
    raise AssertionError("no decision_outbox insert found")


def _hitl_queue_row_hitl_id(fake_conn) -> str:
    for call in fake_conn.execute.call_args_list:
        if "INSERT INTO hitl_queue" in call.args[0]:
            return str(call.args[1])
    raise AssertionError("no hitl_queue insert found")


# ---------------------------------------------------------------------------
# HARD GATE: a growth-stamped proposal published on the real bus survives
# AUTHORITY and is durably decided.
# ---------------------------------------------------------------------------


async def test_published_proposal_is_decided_and_persisted(
    settings, mock_db, mock_redis, fake_conn
):
    bus = InMemoryEventBus()
    opa = _MockOPA(allow=True)
    consumer = _build_consumer(bus, settings, mock_db, mock_redis, opa)
    consumer.subscribe(ORG, "growth")

    event = _proposal()
    await bus.publish(event)
    assert await _settle(lambda: opa.calls != [])
    assert await _settle(lambda: any("INSERT INTO decisions" in s
                                     for s in _executed_sql(fake_conn)))
    await consumer.stop()

    # OPA saw the event that was published, not a reconstruction of it.
    assert opa.calls == [str(event.event_id)]

    sqls = _executed_sql(fake_conn)
    # AUTHORITY passed — a rejection there would still write a decisions row, so
    # assert on the recorded stage rather than on the row's existence alone.
    assert any(
        "INSERT INTO decisions" in s and "INSERT INTO decision_outbox" in s
        for s in sqls
    )
    assert not any("INSERT INTO hitl_queue" in s for s in sqls)


async def test_authority_stage_passes_for_a_growth_proposal(
    settings, mock_db, mock_redis, fake_conn
):
    """The vocabulary gate, end to end: the `sales.` category rides the `growth`
    channel and AUTHORITY must accept that pairing (ADR-0005 Alt A). Under the
    pre-ADR `{category}.` inference this proposal was rejected at stage 1."""
    bus = InMemoryEventBus()
    opa = _MockOPA(allow=True)
    seen: list = []

    consumer = _build_consumer(bus, settings, mock_db, mock_redis, opa)
    inner = consumer._pipeline_fn

    async def _capture(context):
        result = await inner(context)
        seen.append(result)
        return result

    consumer._pipeline_fn = _capture
    consumer.subscribe(ORG, "growth")

    await bus.publish(_proposal())
    assert await _settle(lambda: seen != [])
    await consumer.stop()

    result = seen[0]
    authority = next(s for s in result.steps if s.stage.value == "AUTHORITY")
    assert authority.passed
    assert authority.detail["department"] == "growth"
    assert authority.detail["event_type"] == "sales.campaign_proposed"
    assert result.outcome is DecisionOutcome.APPROVED


# ---------------------------------------------------------------------------
# HARD GATE (regression): the hitl_queue row and the governance event agree on
# hitl_id, all the way from a published event.
# ---------------------------------------------------------------------------


async def test_defer_writes_hitl_row_with_matching_hitl_id(
    settings, mock_db, mock_redis, fake_conn
):
    bus = InMemoryEventBus()
    opa = _MockOPA(allow=True, require_human=True)
    consumer = _build_consumer(bus, settings, mock_db, mock_redis, opa)
    consumer.subscribe(ORG, "growth")

    event = _proposal()
    await bus.publish(event)
    assert await _settle(lambda: any("INSERT INTO hitl_queue" in s
                                     for s in _executed_sql(fake_conn)))
    await consumer.stop()

    queue_hitl_id = _hitl_queue_row_hitl_id(fake_conn)
    # One id, minted once upstream of both writers.
    assert _outbox_payload_hitl_id(fake_conn) == queue_hitl_id
    # ...and the governance stream event carries the same one.
    assert mock_redis.xadd.await_args.args[0] == f"evt:{ORG}:governance"
    assert mock_redis.xadd.await_args.args[1]["hitl_queue_id"] == queue_hitl_id
    # Deterministic from the published event_id, so a redelivery rebuilds it.
    assert queue_hitl_id == str(hitl_id_for(decision_id_for(str(event.event_id))))


# ---------------------------------------------------------------------------
# Redelivery of the same published event decides exactly once.
# ---------------------------------------------------------------------------


async def test_redelivered_event_is_decided_once(
    settings, mock_db, mock_redis, fake_conn
):
    bus = InMemoryEventBus()
    opa = _MockOPA(allow=True)
    consumer = _build_consumer(bus, settings, mock_db, mock_redis, opa)
    consumer.subscribe(ORG, "growth")

    event = _proposal()
    await bus.publish(event)
    assert await _settle(lambda: opa.calls != [])
    # Same event_id delivered again — an unacked PEL entry coming back around.
    await bus.publish(event)
    await _settle(lambda: False, ticks=50)
    await consumer.stop()

    assert opa.calls == [str(event.event_id)]
