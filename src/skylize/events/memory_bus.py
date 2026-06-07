"""
In-memory EventBus — the `memory` backend and test double.

Upholds the EventBus contract: validates against EVENT_REGISTRY on publish,
preserves per-stream order, supports ack and DLQ. Single-process only; the Redis
adapter is the real implementation.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import AsyncIterator

from ..schemas.base import BaseEvent
from ..schemas.events import EVENT_REGISTRY
from .bus import DeliveredEvent, dlq_name, stream_name


class SchemaRejected(Exception):
    pass


class InMemoryEventBus:
    def __init__(self) -> None:
        self._streams: dict[str, deque[tuple[str, BaseEvent]]] = defaultdict(deque)
        self._dlq: dict[str, list[tuple[str, BaseEvent, str]]] = defaultdict(list)
        self._seq = 0
        self._published: list[BaseEvent] = []  # full ordered log, for assertions
        self._cond = asyncio.Condition()

    # -- producer -----------------------------------------------------------
    async def publish(self, event: BaseEvent) -> str:
        if event.type not in EVENT_REGISTRY:
            # Never silently dropped — would route to DLQ + audit.schema_rejected.
            raise SchemaRejected(f"unknown event type: {event.type}")
        self._seq += 1
        msg_id = f"{self._seq}-0"
        stream = stream_name(event.tenant_id, event.department)
        async with self._cond:
            self._streams[stream].append((msg_id, event))
            self._published.append(event)
            self._cond.notify_all()
        return msg_id

    # -- consumer -----------------------------------------------------------
    async def consume(
        self, *, tenant_id: str, department: str, group: str, consumer: str
    ) -> AsyncIterator[DeliveredEvent]:
        stream = stream_name(tenant_id, department)
        while True:
            async with self._cond:
                while not self._streams[stream]:
                    await self._cond.wait()
                msg_id, event = self._streams[stream].popleft()
            yield DeliveredEvent(stream=stream, message_id=msg_id, event=event)

    async def ack(self, delivered: DeliveredEvent, *, group: str) -> None:
        return None  # popleft already removed it from the in-memory stream

    async def to_dlq(self, delivered: DeliveredEvent, *, reason: str) -> None:
        dlq = dlq_name(delivered.event.tenant_id, delivered.event.department)
        self._dlq[dlq].append((delivered.message_id, delivered.event, reason))

    # -- test/inspection helpers -------------------------------------------
    def published_of_type(self, type_: str) -> list[BaseEvent]:
        return [e for e in self._published if e.type == type_]

    @property
    def dlq(self) -> dict[str, list[tuple[str, BaseEvent, str]]]:
        return self._dlq
