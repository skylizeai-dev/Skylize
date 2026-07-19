"""DecisionEngineConsumer — transport tests against the EventBus port.

The predecessor of this file mocked ``redis.asyncio`` and asserted XREADGROUP /
XACK / XAUTOCLAIM calls against streams named after event types. Those streams do
not exist on the live bus, so every one of those tests could pass while the
consumer read nothing (ADR-0005). These tests drive the real ``InMemoryEventBus``
instead: a proposal is *published* the way a producing agent publishes it, and
the assertion is that it reaches the pipeline.
"""
from __future__ import annotations

import asyncio
import pathlib
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest

from skylize.decision_engine import consumer as consumer_module
from skylize.decision_engine.constants import SUBSCRIBED_DEPARTMENTS
from skylize.decision_engine.consumer import DecisionEngineConsumer
from skylize.decision_engine.models import DecisionContext, DecisionOutcome
from skylize.events.bus import DeliveredEvent, stream_name
from skylize.events.memory_bus import InMemoryEventBus
from skylize.schemas.base import BaseEvent
from skylize.schemas.events.sales import SalesCampaignProposed, SalesPerformanceIngested

from .conftest import make_decision_result

ORG = "org_test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _campaign_proposal(department: str = "growth") -> SalesCampaignProposed:
    """A real spend-bearing proposal, stamped the way `director_growth` stamps it.

    ADR-0005: `sales.campaign_proposed` carries the `sales.` category but rides
    the `growth` department channel, because the growth director produces it.
    """
    return SalesCampaignProposed(
        tenant_id=ORG,
        partition_key="campaign:c1",
        department=department,
        correlation_id=uuid4(),
        payload=SalesCampaignProposed.Payload(
            campaign_id="c1",
            channel="meta",
            proposed_budget_minor_units=250_000,
            currency="USD",
            objective="conversions",
        ),
    )


def _performance_event() -> SalesPerformanceIngested:
    """A non-proposal event that also rides `evt:{org}:growth`."""
    return SalesPerformanceIngested(
        tenant_id=ORG,
        partition_key="campaign:c1",
        department="growth",
        correlation_id=uuid4(),
        payload=SalesPerformanceIngested.Payload(
            campaign_id="c1", channel="meta", spend_minor_units=1000,
            impressions=10, clicks=1, conversions=0, roas=0.0,
        ),
    )


class _RecordingBus:
    """EventBus double that records what was subscribed to, and yields nothing."""

    def __init__(self) -> None:
        self.subscriptions: list[tuple[str, str, str, str]] = []

    async def publish(self, event: BaseEvent) -> str:  # pragma: no cover - unused
        return "1-0"

    async def consume(
        self, *, tenant_id: str, department: str, group: str, consumer: str
    ) -> AsyncIterator[DeliveredEvent]:
        self.subscriptions.append((tenant_id, department, group, consumer))
        await asyncio.Event().wait()  # park forever, like an idle stream
        yield  # pragma: no cover - unreachable, makes this an async generator

    async def ack(self, delivered: DeliveredEvent, *, group: str) -> None: ...

    async def to_dlq(self, delivered: DeliveredEvent, *, reason: str) -> None: ...


def _pipeline_spy(outcome: DecisionOutcome = DecisionOutcome.APPROVED):
    """Returns (fn, seen_contexts). The fn has the exact pipeline_fn shape."""
    seen: list[DecisionContext] = []

    async def _fn(context: DecisionContext):
        seen.append(context)
        return make_decision_result(outcome=outcome, event_id=context.event_id)

    return _fn, seen


async def _settle(predicate, *, ticks: int = 200) -> bool:
    """Yield to the loop until `predicate()` holds (or we run out of patience)."""
    for _ in range(ticks):
        if predicate():
            return True
        await asyncio.sleep(0)
    return predicate()


# ---------------------------------------------------------------------------
# Subscription model: per (org, department), on department channels
# ---------------------------------------------------------------------------


async def test_subscribes_one_consumer_per_org_and_served_department(settings):
    bus = _RecordingBus()
    fn, _ = _pipeline_spy()
    consumer = DecisionEngineConsumer(bus, settings, fn)

    consumer.subscribe(ORG)
    await _settle(lambda: len(bus.subscriptions) == len(SUBSCRIBED_DEPARTMENTS))
    await consumer.stop()

    subscribed_departments = {dept for _org, dept, _g, _c in bus.subscriptions}
    assert subscribed_departments == set(SUBSCRIBED_DEPARTMENTS)
    assert {org for org, *_ in bus.subscriptions} == {ORG}
    assert {group for *_, group, _c in bus.subscriptions} == {
        settings.redis_consumer_group
    }


async def test_start_subscribes_each_org_department_pair(settings):
    bus = _RecordingBus()
    fn, _ = _pipeline_spy()
    consumer = DecisionEngineConsumer(bus, settings, fn)

    await consumer.start([("org_a", "growth"), ("org_b", "creative")])
    await _settle(lambda: len(bus.subscriptions) == 2)
    await consumer.stop()

    assert {(org, dept) for org, dept, _g, _c in bus.subscriptions} == {
        ("org_a", "growth"),
        ("org_b", "creative"),
    }


async def test_subscriptions_are_department_channels_not_event_types(settings):
    """ADR-0005 regression gate. The consumer used to read streams named after
    event types (`sales.campaign_proposed`); the bus keys everything as
    `evt:{tenant}:{department}` and nothing else."""
    bus = _RecordingBus()
    fn, _ = _pipeline_spy()
    consumer = DecisionEngineConsumer(bus, settings, fn)

    consumer.subscribe(ORG)
    await _settle(lambda: len(bus.subscriptions) == len(SUBSCRIBED_DEPARTMENTS))
    await consumer.stop()

    keys = {stream_name(org, dept) for org, dept, _g, _c in bus.subscriptions}
    assert keys == {f"evt:{ORG}:{d}" for d in SUBSCRIBED_DEPARTMENTS}
    # No subscription is keyed on an event type — the old, unreachable taxonomy.
    assert not any("." in dept for _o, dept, _g, _c in bus.subscriptions)


async def test_consumer_names_are_unique_per_subscription(settings):
    """Two routers sharing a Redis consumer name would inherit each other's PEL."""
    bus = _RecordingBus()
    fn, _ = _pipeline_spy()
    consumer = DecisionEngineConsumer(bus, settings, fn)

    await consumer.start([("org_a", "growth"), ("org_b", "growth")])
    await _settle(lambda: len(bus.subscriptions) == 2)
    await consumer.stop()

    names = [name for *_, name in bus.subscriptions]
    assert len(set(names)) == len(names)


# ---------------------------------------------------------------------------
# Delivery: a real published proposal reaches the pipeline
# ---------------------------------------------------------------------------


async def test_published_growth_proposal_reaches_the_pipeline(settings):
    bus = InMemoryEventBus()
    fn, seen = _pipeline_spy()
    consumer = DecisionEngineConsumer(bus, settings, fn)
    consumer.subscribe(ORG, "growth")

    event = _campaign_proposal()
    await bus.publish(event)
    assert await _settle(lambda: len(seen) == 1)
    await consumer.stop()

    context = seen[0]
    assert context.event_id == str(event.event_id)
    assert context.tenant_id == ORG
    assert context.department == "growth"
    assert context.event_type == "sales.campaign_proposed"
    assert context.payload["payload"]["campaign_id"] == "c1"


async def test_context_payload_is_json_native(settings):
    """The payload is POSTed to OPA by httpx (stdlib encoder — no UUID support)
    and written to hitl_queue.proposal_json, so it must carry no raw UUIDs."""
    bus = InMemoryEventBus()
    fn, seen = _pipeline_spy()
    consumer = DecisionEngineConsumer(bus, settings, fn)
    consumer.subscribe(ORG, "growth")

    await bus.publish(_campaign_proposal())
    assert await _settle(lambda: len(seen) == 1)
    await consumer.stop()

    assert not any(isinstance(v, UUID) for v in seen[0].payload.values())
    assert isinstance(seen[0].payload["event_id"], str)


async def test_unaddressed_event_on_a_watched_channel_is_ignored(settings):
    """`sales.performance_ingested` rides the growth channel but is not a
    proposal. Feeding it to the pipeline would manufacture a REJECTED decision
    at the AUTHORITY stage for an event nobody proposed."""
    bus = InMemoryEventBus()
    fn, seen = _pipeline_spy()
    consumer = DecisionEngineConsumer(bus, settings, fn)
    consumer.subscribe(ORG, "growth")

    await bus.publish(_performance_event())
    await bus.publish(_campaign_proposal())

    # The proposal published *after* it still lands — the ignore is a skip, not a stall.
    assert await _settle(lambda: len(seen) == 1)
    await consumer.stop()
    assert [c.event_type for c in seen] == ["sales.campaign_proposed"]


async def test_proposal_on_the_wrong_department_is_not_addressed(settings):
    """The table pairs type WITH department: `sales.campaign_proposed` belongs to
    growth, so the same type on the `sales` (SDR) channel is not the engine's."""
    fn, seen = _pipeline_spy()
    consumer = DecisionEngineConsumer(_RecordingBus(), settings, fn)

    await consumer._handle_event(_campaign_proposal(department="sales"))

    assert seen == []


# ---------------------------------------------------------------------------
# Idempotency — the ProcessedEventStore port, not a Redis SETNX key
# ---------------------------------------------------------------------------


async def test_already_processed_event_skips_the_pipeline(settings):
    from skylize.dal.memory import InMemoryProcessedEventStore

    store = InMemoryProcessedEventStore()
    fn, seen = _pipeline_spy()
    consumer = DecisionEngineConsumer(
        _RecordingBus(), settings, fn, processed=store
    )
    event = _campaign_proposal()
    await store.mark_processed(str(event.event_id), "APPROVED", org_id=ORG)

    await consumer._handle_event(event)

    assert seen == []


async def test_processed_is_marked_with_the_outcome_after_success(settings):
    from skylize.dal.memory import InMemoryProcessedEventStore

    store = InMemoryProcessedEventStore()
    fn, _ = _pipeline_spy(outcome=DecisionOutcome.DEFERRED_TO_HUMAN)
    consumer = DecisionEngineConsumer(
        _RecordingBus(), settings, fn, processed=store
    )
    event = _campaign_proposal()

    await consumer._handle_event(event)

    assert await store.is_processed(str(event.event_id), org_id=ORG)


async def test_not_marked_processed_when_the_pipeline_raises(settings):
    """A crash mid-decision must redeliver, not silently swallow the proposal."""
    from skylize.dal.memory import InMemoryProcessedEventStore

    store = InMemoryProcessedEventStore()

    async def _boom(context: DecisionContext):
        raise RuntimeError("db down")

    consumer = DecisionEngineConsumer(
        _RecordingBus(), settings, _boom, processed=store
    )
    event = _campaign_proposal()

    with pytest.raises(RuntimeError):
        await consumer._handle_event(event)

    assert not await store.is_processed(str(event.event_id), org_id=ORG)


# ---------------------------------------------------------------------------
# Failure handling is the router's: no ack, then DLQ after max retries
# ---------------------------------------------------------------------------


async def test_repeated_failure_routes_to_the_department_dlq(settings):
    bus = InMemoryEventBus()

    async def _boom(context: DecisionContext):
        raise RuntimeError("opa unreachable")

    consumer = DecisionEngineConsumer(bus, settings, _boom)
    consumer.subscribe(ORG, "growth")
    event = _campaign_proposal()

    # The in-memory bus pops on read, so redelivery is modelled by republishing
    # the same event_id — which is what an unacked Redis PEL entry becomes.
    for _ in range(settings.redis_max_retries):
        await bus.publish(event)
        await _settle(lambda: False, ticks=20)

    await consumer.stop()
    dlq = bus.dlq[f"dlq:{ORG}:growth"]
    assert len(dlq) == 1
    assert "handler_failed" in dlq[0][2]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_run_without_org_ids_refuses_to_start(settings):
    fn, _ = _pipeline_spy()
    consumer = DecisionEngineConsumer(_RecordingBus(), settings, fn)

    with pytest.raises(RuntimeError, match="no org_ids"):
        await consumer.run([])


async def test_run_subscribes_every_org_department_pair(settings):
    bus = _RecordingBus()
    fn, _ = _pipeline_spy()
    consumer = DecisionEngineConsumer(bus, settings, fn)

    task = asyncio.create_task(consumer.run(["org_a", "org_b"]))
    expected = 2 * len(SUBSCRIBED_DEPARTMENTS)
    assert await _settle(lambda: len(bus.subscriptions) == expected)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_stop_cancels_every_subscription(settings):
    bus = _RecordingBus()
    fn, _ = _pipeline_spy()
    consumer = DecisionEngineConsumer(bus, settings, fn)
    consumer.subscribe(ORG)
    await _settle(lambda: len(bus.subscriptions) == len(SUBSCRIBED_DEPARTMENTS))

    await consumer.stop()

    assert consumer._tasks == []
    assert consumer._routers == []


# ---------------------------------------------------------------------------
# HARD GATE: the transport is the port, not a Redis client
# ---------------------------------------------------------------------------


def test_consumer_module_holds_no_redis_client():
    """ADR-0005: the consumer talks to the EventBus port only, so a Kafka/NATS
    migration stays an adapter swap. Source-level, because an unused import is
    exactly the kind of thing that creeps back in."""
    source = pathlib.Path(consumer_module.__file__).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "import redis" not in code
    assert "aioredis" not in code
    assert not hasattr(consumer_module, "redis")


def test_subscribed_streams_alias_is_gone():
    """It aliased event-type names as if they were stream keys. ADR-0005 says
    delete, not re-point."""
    from skylize.decision_engine import constants

    assert not hasattr(constants, "SUBSCRIBED_STREAMS")
