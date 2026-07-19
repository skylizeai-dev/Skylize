"""DecisionEngineConsumer against REAL Redis Streams — no InMemoryEventBus.

The existing consumer tests (tests/decision_engine/test_consumer.py) drive
`InMemoryEventBus`, which pops on read and acks by no-op. That proves the
consumer's dispatch logic but says nothing about the parts only Redis has:
consumer-group creation, the `evt:{tenant}:{department}` key actually matching
what a producer publishes to, JSON round-tripping through stream fields back into
a typed event, and PEL/ack behaviour.

OPA is still mocked here, and deliberately so: `pipeline_fn` is the seam the
consumer is defined against, and no OPA server exists to point at. What that
leaves untested is stated plainly rather than implied — see the module note in
tests/decision_engine/test_opa_client_integration.py and the session report.

Skipped unless SKYLIZE_TEST_REDIS_URL is set (see conftest), matching every other
integration module here.

NEVER YET EXECUTED AGAINST A REAL REDIS. Written on a machine with no Redis, no
Docker and no `redis-server` binary, so every assertion below is verified only by
reading the adapter. CI's `integration` job supplies a Redis service container
(.github/workflows/ci.yml) and will be the FIRST real run — treat a failure there
as this file being wrong, not as a regression in the consumer, until proven
otherwise. Saying so explicitly because a test whose fixture cannot occur is the
exact failure mode this module exists to correct, and an unexecuted test asserting
it has coverage would repeat it one level up.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from skylize.dal.memory import InMemoryProcessedEventStore
from skylize.decision_engine.config import DecisionEngineSettings
from skylize.decision_engine.constants import SUBSCRIBED_DEPARTMENTS
from skylize.decision_engine.consumer import DecisionEngineConsumer
from skylize.decision_engine.models import (
    DecisionContext,
    DecisionOutcome,
    DecisionResult,
)
from skylize.decision_engine.pipeline import hitl_id_for
from skylize.events.bus import stream_name
from skylize.events.redis_adapter import RedisEventBus
from skylize.schemas.events.governance import GovernanceHumanApprovalReceived
from skylize.schemas.events.sales import SalesCampaignProposed, SalesPerformanceIngested

from .conftest import REDIS_URL, requires_redis

pytestmark = [pytest.mark.integration, requires_redis]

ORG = "org_redis_de"


def _settings() -> DecisionEngineSettings:
    return DecisionEngineSettings(
        redis_url=REDIS_URL or "redis://localhost:6379",
        redis_consumer_group="cg:decision_engine_it",
        redis_consumer_name="it-consumer",
        redis_max_retries=3,
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",
        database_url="postgresql://test:test@localhost/test",
    )


def _proposal() -> SalesCampaignProposed:
    """A real spend proposal, stamped the way `director_growth` stamps it."""
    return SalesCampaignProposed(
        tenant_id=ORG,
        partition_key="campaign:c1",
        department="growth",
        correlation_id=uuid.uuid4(),
        payload=SalesCampaignProposed.Payload(
            campaign_id="c1",
            channel="meta",
            proposed_budget_minor_units=250_000,
            currency="USD",
            objective="conversions",
        ),
    )


def _noise() -> SalesPerformanceIngested:
    """A non-proposal that rides the SAME `evt:{org}:growth` channel."""
    return SalesPerformanceIngested(
        tenant_id=ORG,
        partition_key="campaign:c1",
        department="growth",
        correlation_id=uuid.uuid4(),
        payload=SalesPerformanceIngested.Payload(
            campaign_id="c1", channel="meta", spend_minor_units=1000,
            impressions=10, clicks=1, conversions=0, roas=0.0,
        ),
    )


def _verdict(decision_id: uuid.UUID) -> GovernanceHumanApprovalReceived:
    return GovernanceHumanApprovalReceived(
        tenant_id=ORG,
        partition_key=str(decision_id),
        department="governance",
        correlation_id=uuid.uuid4(),
        payload=GovernanceHumanApprovalReceived.Payload(
            decision_id=decision_id,
            hitl_id=hitl_id_for(str(decision_id)),
            approved=True,
            decided_by="owner@skylize.test",
        ),
    )


def _spy(outcome: DecisionOutcome = DecisionOutcome.APPROVED):
    seen: list[DecisionContext] = []

    async def _fn(context: DecisionContext) -> DecisionResult:
        seen.append(context)
        return DecisionResult(
            decision_id=str(uuid.uuid4()),
            event_id=context.event_id,
            tenant_id=context.tenant_id,
            outcome=outcome,
            scoring=None,
            capital=None,
            final_reason="integration",
            steps=[],
            evaluated_at=datetime.now(timezone.utc),
        )

    return _fn, seen


async def _ready(bus: RedisEventBus, settings: DecisionEngineSettings, *departments):
    """Create the consumer groups BEFORE anything is published.

    Not a nicety. `consume` reads `">"`, which delivers only messages added after
    the group exists, so publishing first would drop the event and the test would
    fail for a reason unrelated to what it asserts. Sleeping until the router got
    around to `ensure_group` would make that a race; doing it explicitly here is
    deterministic. `ensure_group` swallows BUSYGROUP (redis_adapter.py:41-43), so
    the router creating it again is harmless.
    """
    for department in departments:
        await bus.ensure_group(
            tenant_id=ORG, department=department, group=settings.redis_consumer_group
        )


async def _wait_for(predicate, *, timeout: float = 10.0) -> bool:
    """Poll until predicate holds. Real Redis needs wall-clock, not loop ticks."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


async def _pending_count(client, tenant: str, department: str, group: str) -> int:
    summary = await client.xpending(stream_name(tenant, department), group)
    return int(summary["pending"]) if summary else 0


# ---------------------------------------------------------------------------
# The round trip InMemoryEventBus cannot prove
# ---------------------------------------------------------------------------


async def test_published_proposal_reaches_the_pipeline_over_real_streams(redis_client):
    """Producer XADDs to evt:{org}:growth; the consumer decodes and dispatches it."""
    settings = _settings()
    bus = RedisEventBus(REDIS_URL)
    pipeline_fn, seen = _spy()
    consumer = DecisionEngineConsumer(
        bus, settings, pipeline_fn, processed=InMemoryProcessedEventStore()
    )
    try:
        await _ready(bus, settings, "growth")
        consumer.subscribe(ORG, "growth")
        event = _proposal()
        await bus.publish(event)

        assert await _wait_for(lambda: len(seen) == 1), "proposal never arrived"
        assert seen[0].event_id == str(event.event_id)
        assert seen[0].tenant_id == ORG
        assert seen[0].department == "growth"
        assert seen[0].event_type == "sales.campaign_proposed"
        # The payload survived JSON round-tripping through the stream fields.
        assert seen[0].payload["payload"]["campaign_id"] == "c1"
    finally:
        await consumer.stop()
        await bus.close()


async def test_non_proposal_on_the_same_channel_is_ignored_and_acked(redis_client):
    """The addressing filter, against a real PEL.

    In-memory cannot show this: an ignored event must still be ACKed, or it
    accumulates in the consumer group's pending list forever.
    """
    settings = _settings()
    bus = RedisEventBus(REDIS_URL)
    pipeline_fn, seen = _spy()
    consumer = DecisionEngineConsumer(
        bus, settings, pipeline_fn, processed=InMemoryProcessedEventStore()
    )
    try:
        await _ready(bus, settings, "growth")
        consumer.subscribe(ORG, "growth")
        await bus.publish(_noise())
        proposal = _proposal()
        await bus.publish(proposal)

        # The proposal arriving proves the noise was consumed and passed over.
        assert await _wait_for(lambda: len(seen) == 1)
        assert seen[0].event_id == str(proposal.event_id)

        await asyncio.sleep(0.3)  # let the acks for both messages settle
        pending = await _pending_count(
            redis_client, ORG, "growth", settings.redis_consumer_group
        )
        assert pending == 0, "an ignored event must be acked, not left pending"
    finally:
        await consumer.stop()
        await bus.close()


async def test_duplicate_event_id_is_evaluated_once(redis_client):
    """Idempotency through ProcessedEventStore, over a real stream."""
    settings = _settings()
    bus = RedisEventBus(REDIS_URL)
    pipeline_fn, seen = _spy()
    consumer = DecisionEngineConsumer(
        bus, settings, pipeline_fn, processed=InMemoryProcessedEventStore()
    )
    try:
        await _ready(bus, settings, "growth")
        consumer.subscribe(ORG, "growth")
        event = _proposal()
        await bus.publish(event)
        assert await _wait_for(lambda: len(seen) == 1)
        await bus.publish(event)  # same event_id, second XADD
        await asyncio.sleep(0.5)

        assert len(seen) == 1, "a redelivered proposal must not be decided twice"
    finally:
        await consumer.stop()
        await bus.close()


# ---------------------------------------------------------------------------
# The governance channel — this session's HITL resume path, over real Redis
# ---------------------------------------------------------------------------


async def test_verdict_on_the_governance_channel_resumes_without_the_pipeline(
    redis_client,
):
    """End-to-end proof that subscribing `governance` actually delivers verdicts.

    The unit tests call `_handle_event` directly. This one publishes to the real
    `evt:{org}:governance` stream and asserts the verdict is routed to the resume
    handler and NEVER to the pipeline.
    """
    settings = _settings()
    bus = RedisEventBus(REDIS_URL)
    pipeline_fn, seen = _spy()
    resumed: list[GovernanceHumanApprovalReceived] = []

    async def _resume(event: GovernanceHumanApprovalReceived) -> bool:
        resumed.append(event)
        return True

    consumer = DecisionEngineConsumer(
        bus,
        settings,
        pipeline_fn,
        resume_fn=_resume,
        processed=InMemoryProcessedEventStore(),
    )
    try:
        await _ready(bus, settings, *SUBSCRIBED_DEPARTMENTS)
        consumer.subscribe(ORG)  # all served departments, incl. governance
        decision_id = uuid.uuid4()
        await bus.publish(_verdict(decision_id))

        assert await _wait_for(lambda: len(resumed) == 1), "verdict never arrived"
        assert resumed[0].payload.decision_id == decision_id
        assert resumed[0].payload.hitl_id == hitl_id_for(str(decision_id))
        assert seen == [], "a human verdict must never reach the pipeline"
    finally:
        await consumer.stop()
        await bus.close()


# ---------------------------------------------------------------------------
# CHARACTERIZATION: the queued delivery gap, made regression-visible
# ---------------------------------------------------------------------------


async def test_failed_handler_strands_the_message_in_the_pel_no_redelivery(
    redis_client,
):
    """Documents CURRENT behaviour, which is not the intended behaviour.

    `RedisEventBus.consume` reads `{stream: ">"}` only and never reclaims
    (redis_adapter.py:55), so a message whose handler raised is left un-acked in
    the PEL and is never re-read. The router's retry/DLQ budget therefore cannot
    be reached (see the delivery-semantics note in router.py).

    This test asserts the gap exists, so it FAILS LOUDLY the day reclaim is
    implemented — at which point it should be rewritten to assert redelivery and
    eventual DLQ, not deleted. Making the gap executable is the point: it was
    previously documented as working, and two green tests appeared to confirm it
    by manufacturing a redelivery the bus cannot produce.
    """
    settings = _settings()
    bus = RedisEventBus(REDIS_URL)
    attempts: list[str] = []

    async def _boom(context: DecisionContext) -> DecisionResult:
        attempts.append(context.event_id)
        raise RuntimeError("pipeline failure")

    consumer = DecisionEngineConsumer(
        bus, settings, _boom, processed=InMemoryProcessedEventStore()
    )
    try:
        await _ready(bus, settings, "growth")
        consumer.subscribe(ORG, "growth")
        await bus.publish(_proposal())

        assert await _wait_for(lambda: len(attempts) == 1)
        # Well past any plausible redelivery interval.
        await asyncio.sleep(2.0)

        assert len(attempts) == 1, (
            "CURRENT behaviour is no redelivery. More than one attempt means "
            "reclaim now exists — rewrite this test to assert retry-then-DLQ."
        )
        pending = await _pending_count(
            redis_client, ORG, "growth", settings.redis_consumer_group
        )
        assert pending == 1, "the failed message is stranded, unacked, in the PEL"

        dlq = await redis_client.xlen(f"dlq:{ORG}:growth")
        assert dlq == 0, "and it never reaches the DLQ, because it is never retried"
    finally:
        await consumer.stop()
        await bus.close()
