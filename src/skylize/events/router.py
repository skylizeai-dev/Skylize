"""
Event Router — consumes a department stream and dispatches to a handler.

Implements idempotency on `event_id` and, given a redelivering bus, DLQ after
`dlq_after_retries`. The handler is any async callable taking a typed
`BaseEvent`. One router instance serves one (department, consumer group).

DELIVERY SEMANTICS — WHAT THIS ACTUALLY DOES TODAY, which is not what
decision_flow.md §8 specifies. The retry/DLQ budget below is UNREACHABLE against
the only production bus. `RedisEventBus.consume` reads `{stream: ">"}` — new
messages only (redis_adapter.py:55) — and there is no XAUTOCLAIM/XPENDING
anywhere in the adapter, so a message left un-acked by a failing handler is never
re-read: not by another worker, not by this one after a restart. It sits in the
PEL forever. `_attempts[event_id]` therefore never exceeds 1, and the
`>= dlq_after` branch cannot fire for a handler failure.

Effective semantics are at-MOST-once for failures, not at-least-once. Both tests
covering the DLQ path manufacture the redelivery the bus cannot produce — one
calls `_dispatch` directly (tests/integration/test_event_router.py:57), the other
republishes the event (tests/decision_engine/test_consumer.py:324) — so they pass
without proving the path is reachable.

Closing this needs a durable delivery count on `DeliveredEvent` (bus.py has no
such field) and a reclaim path on the shared adapter, which would change the
inline engine's emission behaviour at the same time — it is the sole EventRouter
consumer besides the OPA engine (app/decision_engine/engine.py:104). That makes
it a design decision rather than a patch; it is queued, not fixed here. Until it
lands, do not rely on redelivery for correctness anywhere.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable

from ..schemas.base import BaseEvent
from .bus import DeliveredEvent, EventBus

log = logging.getLogger("skylize.events.router")

EventHandler = Callable[[BaseEvent], Awaitable[None]]


class EventRouter:
    def __init__(
        self,
        bus: EventBus,
        *,
        group: str,
        consumer: str = "worker-1",
        dlq_after_retries: int = 5,
    ) -> None:
        self._bus = bus
        self._group = group
        self._consumer = consumer
        self._dlq_after = dlq_after_retries
        self._handler: EventHandler | None = None
        self._seen: set[str] = set()  # event_ids already processed (idempotency)
        self._attempts: dict[str, int] = defaultdict(int)
        self._stop = False

    def on_event(self, handler: EventHandler) -> None:
        self._handler = handler

    def stop(self) -> None:
        self._stop = True

    async def run(self, *, tenant_id: str, department: str) -> None:
        if self._handler is None:
            raise RuntimeError("no handler registered; call on_event() first")
        stream = self._bus.consume(
            tenant_id=tenant_id, department=department,
            group=self._group, consumer=self._consumer,
        )
        async for delivered in stream:
            if self._stop:
                break
            await self._dispatch(delivered)

    async def _dispatch(self, delivered: DeliveredEvent) -> None:
        event_id = str(delivered.event.event_id)
        if event_id in self._seen:  # idempotent: already decided
            await self._bus.ack(delivered, group=self._group)
            return
        try:
            assert self._handler is not None
            await self._handler(delivered.event)
            self._seen.add(event_id)
            await self._bus.ack(delivered, group=self._group)
        except Exception as exc:  # noqa: BLE001 — router must never crash the loop
            self._attempts[event_id] += 1
            log.warning(
                "event handler failed event_id=%s attempt=%d: %s",
                event_id, self._attempts[event_id], exc,
            )
            if self._attempts[event_id] >= self._dlq_after:
                await self._bus.to_dlq(delivered, reason=f"handler_failed: {exc}")
                await self._bus.ack(delivered, group=self._group)
            # else: no ack. NOTE this does NOT currently cause a redelivery —
            # RedisEventBus.consume reads ">" only (redis_adapter.py:55), so the
            # message stays in the PEL unread and this branch strands it. See
            # the delivery-semantics note in the module docstring.
