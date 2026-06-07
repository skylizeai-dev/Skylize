"""Event router: dispatch, idempotency on event_id, DLQ after retries."""

from __future__ import annotations

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
    bus = InMemoryEventBus()
    router = EventRouter(bus, group="cg:test", dlq_after_retries=3)

    async def boom(ev) -> None:
        raise RuntimeError("handler failure")

    router.on_event(boom)
    event = _event()
    delivered = _delivered(event)

    for _ in range(3):
        await router._dispatch(delivered)

    dlq = bus.dlq["dlq:org_1:creative"]
    assert len(dlq) == 1
    assert "handler_failed" in dlq[0][2]
