"""
Event Router — consumes a department stream and dispatches to a handler.

Enforces the delivery semantics from decision_flow.md §8: idempotency on
`event_id`, at-least-once delivery, and DLQ after `dlq_after_retries`. The
handler is any async callable taking a typed `BaseEvent`. One router instance
serves one (department, consumer group).
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
            # else: no ack → redelivery
