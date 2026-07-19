"""
Redis integration (Sprint-2 Task 5) — REAL Redis Streams, no mocks.

Verifies the bus guarantees the Sprint-1 audit found untested:
  - publish → consume round-trip via XADD / XREADGROUP;
  - ack removes the message from the consumer-group PEL;
  - a poisoned (unregistered-type) entry routes to the DLQ, not silently dropped;
  - redelivery: an unacked message is reclaimed from the PEL and handed back by
    the adapter itself (at-least-once), while an acked one is not;
  - the reclaim idle window: an in-flight message is not stolen from its owner.

It also smoke-tests the Redis governance broadcast (Task 2 propagation seam).

Skipped unless SKYLIZE_TEST_REDIS_URL is set (see conftest). CI's `integration`
job provides a Redis service container.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from uuid import uuid4

import pytest

from skylize.events.bus import stream_name
from skylize.events.redis_adapter import RedisEventBus
from skylize.schemas.events.creative import CreativeHooksGenerated

from .conftest import REDIS_URL, requires_redis

pytestmark = [pytest.mark.integration, requires_redis]

GROUP = "cg:test"
DEPT = "creative"


def _event(tenant: str = "org_a") -> CreativeHooksGenerated:
    return CreativeHooksGenerated(
        tenant_id=tenant, partition_key="brief:1", department=DEPT,
        correlation_id=uuid4(),
        payload=CreativeHooksGenerated.Payload(
            brief_id=uuid4(), hooks=["a"], model_used="stub", token_cost=0
        ),
    )


async def _first(bus: RedisEventBus, tenant: str, consumer: str):
    """Pull exactly one delivered event from the consume() async generator."""
    agen = bus.consume(tenant_id=tenant, department=DEPT, group=GROUP, consumer=consumer)
    try:
        return await asyncio.wait_for(agen.__anext__(), timeout=10)
    finally:
        await agen.aclose()


async def test_publish_consume_ack_clears_pel(redis_client) -> None:
    bus = RedisEventBus(REDIS_URL)
    try:
        ev = _event()
        await bus.ensure_group(tenant_id=ev.tenant_id, department=DEPT, group=GROUP)
        msg_id = await bus.publish(ev)
        assert msg_id

        delivered = await _first(bus, ev.tenant_id, "c1")
        assert delivered.event.event_id == ev.event_id

        # Before ack: one pending entry in the group's PEL.
        stream = stream_name(ev.tenant_id, DEPT)
        pending = await redis_client.xpending(stream, GROUP)
        assert pending["pending"] == 1

        await bus.ack(delivered, group=GROUP)
        pending_after = await redis_client.xpending(stream, GROUP)
        assert pending_after["pending"] == 0
    finally:
        await bus.close()


async def test_unregistered_type_routes_to_dlq(redis_client) -> None:
    bus = RedisEventBus(REDIS_URL)
    try:
        tenant = "org_a"
        stream = stream_name(tenant, DEPT)
        await bus.ensure_group(tenant_id=tenant, department=DEPT, group=GROUP)
        # Hand-write an entry with an unknown event type (poison).
        await redis_client.xadd(stream, {"event": '{"type":"creative.bogus"}'})

        agen = bus.consume(tenant_id=tenant, department=DEPT, group=GROUP, consumer="c1")
        try:
            # consume() decodes; a poison entry is acked + copied to DLQ and does
            # NOT yield to the handler — so __anext__ times out (nothing delivered).
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(agen.__anext__(), timeout=2)
        finally:
            await agen.aclose()

        dlq = await redis_client.xrange(f"dlq:{tenant}:{DEPT}")
        assert dlq, "poison message was not routed to the DLQ"
        assert any("schema_rejected" in fields.get("reason", "") for _id, fields in dlq)
    finally:
        await bus.close()


async def test_unacked_message_is_redelivered_by_the_adapter(redis_client) -> None:
    """An un-acked message IS re-delivered by `consume()` — at-least-once, for real.

    This is the test that used to only prove Redis *could* reclaim (it called
    XAUTOCLAIM by hand and admitted the adapter never did). It now drives the
    ADAPTER: consumer c1 reads without acking, and a second `consume()` pass
    reclaims the entry from c1's PEL and hands it back. `reclaim_min_idle_ms=0`
    collapses the idle window so the test does not sleep for it; production runs
    the same path with a 60s window.
    """
    bus = RedisEventBus(REDIS_URL, reclaim_min_idle_ms=0)
    try:
        ev = _event()
        await bus.ensure_group(tenant_id=ev.tenant_id, department=DEPT, group=GROUP)
        await bus.publish(ev)

        # Consumer c1 reads but does NOT ack (simulated crash before ack).
        delivered = await _first(bus, ev.tenant_id, "c1")
        assert delivered.event.event_id == ev.event_id

        stream = stream_name(ev.tenant_id, DEPT)
        pending = await redis_client.xpending(stream, GROUP)
        assert pending["pending"] == 1  # still owned by c1 — not lost

        # THE ASSERTION THAT FLIPPED: a different consumer in the same group gets
        # the message back from the adapter, with no hand-written XAUTOCLAIM.
        again = await _first(bus, ev.tenant_id, "c2")
        assert again.event.event_id == ev.event_id, "adapter did not redeliver"

        # And acking the redelivery clears the PEL, so it does not loop forever.
        await bus.ack(again, group=GROUP)
        assert (await redis_client.xpending(stream, GROUP))["pending"] == 0
    finally:
        await bus.close()


async def test_acked_message_is_not_redelivered(redis_client) -> None:
    """Reclaim must not resurrect completed work.

    The other half of the redelivery contract: with the idle window at zero the
    reclaim pass runs on every loop, so if ack did not remove the entry from the
    PEL this would hand the event back and double-process it.
    """
    bus = RedisEventBus(REDIS_URL, reclaim_min_idle_ms=0)
    try:
        ev = _event()
        await bus.ensure_group(tenant_id=ev.tenant_id, department=DEPT, group=GROUP)
        await bus.publish(ev)

        delivered = await _first(bus, ev.tenant_id, "c1")
        await bus.ack(delivered, group=GROUP)

        agen = bus.consume(tenant_id=ev.tenant_id, department=DEPT, group=GROUP,
                           consumer="c2")
        try:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(agen.__anext__(), timeout=3)
        finally:
            await agen.aclose()
    finally:
        await bus.close()


async def test_reclaim_respects_the_idle_window(redis_client) -> None:
    """A freshly-delivered message is NOT stolen from its owner mid-flight.

    Without the idle window, a healthy worker's in-flight message would be
    reclaimed by a sibling the moment it was read — turning every delivery into a
    duplicate. The window is what distinguishes "still working" from "died".
    """
    bus_fast = RedisEventBus(REDIS_URL, reclaim_min_idle_ms=0)
    bus_patient = RedisEventBus(REDIS_URL, reclaim_min_idle_ms=60_000)
    try:
        ev = _event()
        await bus_fast.ensure_group(tenant_id=ev.tenant_id, department=DEPT, group=GROUP)
        await bus_fast.publish(ev)

        delivered = await _first(bus_fast, ev.tenant_id, "c1")  # read, never acked
        assert delivered.event.event_id == ev.event_id

        # c2 has a 60s window and the entry has been idle for milliseconds.
        agen = bus_patient.consume(tenant_id=ev.tenant_id, department=DEPT,
                                   group=GROUP, consumer="c2")
        try:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(agen.__anext__(), timeout=3)
        finally:
            await agen.aclose()
    finally:
        await bus_fast.close()
        await bus_patient.close()


async def test_retry_budget_exhausts_into_the_dlq_against_real_redis(redis_client) -> None:
    """END-TO-END at-least-once: one publish, N real redeliveries, then the DLQ.

    The gate this whole change exists for. Nothing is republished and no dispatch
    is hand-driven: the handler raises, the router declines to ack, and the
    adapter's XAUTOCLAIM pass keeps handing the same PEL entry back until the
    budget is spent. Before reclaim this stalled at attempt 1 forever and the DLQ
    stream stayed empty.
    """
    from skylize.events.router import EventRouter

    bus = RedisEventBus(REDIS_URL, reclaim_min_idle_ms=0)
    router = EventRouter(bus, group=GROUP, consumer="c1", dlq_after_retries=3)
    attempts: list[str] = []

    async def boom(ev) -> None:
        attempts.append(str(ev.event_id))
        raise RuntimeError("handler failure")

    router.on_event(boom)
    ev = _event()
    await bus.ensure_group(tenant_id=ev.tenant_id, department=DEPT, group=GROUP)

    task = asyncio.create_task(router.run(tenant_id=ev.tenant_id, department=DEPT))
    try:
        await bus.publish(ev)  # ONE publish
        dlq: list = []
        for _ in range(200):
            dlq = await redis_client.xrange(f"dlq:{ev.tenant_id}:{DEPT}")
            if dlq:
                break
            await asyncio.sleep(0.05)
    finally:
        router.stop()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    assert dlq, "retry budget never exhausted into the DLQ"
    assert any("handler_failed" in f.get("reason", "") for _i, f in dlq)
    assert attempts == [str(ev.event_id)] * 3, f"expected 3 deliveries, got {attempts}"

    # Budget spent ⇒ router acked ⇒ the PEL is clean, not looping forever.
    stream = stream_name(ev.tenant_id, DEPT)
    assert (await redis_client.xpending(stream, GROUP))["pending"] == 0
    await bus.close()


async def test_governance_broadcast_fans_out(redis_client) -> None:
    """Redis Pub/Sub governance broadcast reaches a subscriber (Task 2)."""
    from skylize.app.governance.broadcast import GovernanceInvalidation, InvalidationKind
    from skylize.events.redis_governance_broadcast import RedisGovernanceBroadcast

    pub = RedisGovernanceBroadcast(REDIS_URL)
    sub = RedisGovernanceBroadcast(REDIS_URL)
    received: list[GovernanceInvalidation] = []

    async def handler(msg: GovernanceInvalidation) -> None:
        received.append(msg)

    task = asyncio.create_task(sub.subscribe(handler))
    try:
        await asyncio.sleep(0.3)  # let the subscription register
        await pub.publish(
            GovernanceInvalidation(kind=InvalidationKind.KILL_TENANT, org_id="org_a", engaged=True)
        )
        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.1)
        assert received and received[0].org_id == "org_a"
    finally:
        task.cancel()
        await pub.close()
        await sub.close()
