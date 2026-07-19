"""Event router: dispatch, idempotency on event_id, DLQ after retries."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from uuid import uuid4

from skylize.events.bus import DeliveredEvent
from skylize.events.memory_bus import InMemoryEventBus
from skylize.events.router import EventRouter
from skylize.schemas.events.creative import CreativeHooksGenerated


def _event() -> CreativeHooksGenerated:
    return CreativeHooksGenerated(
        tenant_id="org_1", partition_key="brief:1", department="creative",
        correlation_id=uuid4(),
        payload=CreativeHooksGenerated.Payload(
            brief_id=uuid4(), hooks=["a"], model_used="stub", token_cost=0
        ),
    )


def _delivered(event) -> DeliveredEvent:
    return DeliveredEvent(stream="evt:org_1:creative", message_id="1-0", event=event)


async def test_dispatch_calls_handler_and_is_idempotent() -> None:
    bus = InMemoryEventBus()
    router = EventRouter(bus, group="cg:test")
    calls: list[str] = []

    async def handler(ev) -> None:
        calls.append(str(ev.event_id))

    router.on_event(handler)
    event = _event()
    delivered = _delivered(event)

    await router._dispatch(delivered)
    await router._dispatch(delivered)  # redelivery of same event_id

    assert len(calls) == 1  # handled exactly once (idempotent)


async def test_failing_handler_routes_to_dlq_after_retries() -> None:
    """The retry budget is reachable THROUGH THE BUS, not by hand.

    This used to call `_dispatch` directly three times, which proved the counting
    arithmetic but not that anything could ever produce the second dispatch. It
    now publishes ONCE and lets `router.run()` consume: the handler raises, the
    router declines to ack, and the bus redelivers the un-acked entry from its
    pending list until the budget is spent and the router DLQs it. The Redis
    adapter reaches the same state via XAUTOCLAIM over the group PEL.
    """
    bus = InMemoryEventBus()
    router = EventRouter(bus, group="cg:test", dlq_after_retries=3)
    attempts: list[str] = []

    async def boom(ev) -> None:
        attempts.append(str(ev.event_id))
        raise RuntimeError("handler failure")

    router.on_event(boom)
    event = _event()

    task = asyncio.create_task(router.run(tenant_id="org_1", department="creative"))
    try:
        await bus.publish(event)  # published once — every later delivery is a retry
        dlq = bus.dlq["dlq:org_1:creative"]
        for _ in range(200):
            if dlq:
                break
            await asyncio.sleep(0.01)
    finally:
        router.stop()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    assert len(dlq) == 1, "retry budget never exhausted into the DLQ"
    assert "handler_failed" in dlq[0][2]
    # Three deliveries of ONE publish: the budget counted actual redeliveries.
    assert attempts == [str(event.event_id)] * 3


async def test_redelivery_does_not_double_process_a_successful_event() -> None:
    """Redelivery must not resurrect work the handler already completed.

    The safety counterpart to the test above: a handler that succeeds gets acked,
    leaves the pending list, and is never handed back — so enabling redelivery
    does not turn every success into a duplicate.
    """
    bus = InMemoryEventBus()
    router = EventRouter(bus, group="cg:test")
    calls: list[str] = []

    async def handler(ev) -> None:
        calls.append(str(ev.event_id))

    router.on_event(handler)
    event = _event()

    task = asyncio.create_task(router.run(tenant_id="org_1", department="creative"))
    try:
        await bus.publish(event)
        for _ in range(200):
            if calls:
                break
            await asyncio.sleep(0.01)
        # Give the consume loop ample opportunity to redeliver if ack failed.
        await asyncio.sleep(0.1)
    finally:
        router.stop()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    assert calls == [str(event.event_id)]
