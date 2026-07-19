"""
In-memory EventBus — the `memory` backend and test double.

Upholds the EventBus contract: validates against EVENT_REGISTRY on publish,
preserves per-stream order, supports ack and DLQ, and — like the Redis adapter —
redelivers un-acked messages. Single-process only; the Redis adapter is the real
implementation.

REDELIVERY IS MODELLED, NOT TIMED. A consumed message moves into a per-(stream,
group) pending list and leaves it only on ack. Once the stream has no unread
entries, `consume` re-yields the pending ones instead of blocking, which is the
in-memory analogue of the Redis adapter's XAUTOCLAIM pass with the idle window
collapsed to zero. That keeps the router's retry/DLQ budget exercisable in a
plain unit test while remaining an honest model of at-least-once: a handler that
never acks sees the message again, exactly as it would against Redis.
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
        # (stream, group) -> msg_id -> event. The pending-entries list: consumed
        # but not yet acked. Insertion order is delivery order, so redelivery
        # replays oldest-first the way an XAUTOCLAIM cursor sweep does.
        self._pending: dict[tuple[str, str], dict[str, BaseEvent]] = defaultdict(dict)
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
        pending = self._pending[(stream, group)]
        while True:
            async with self._cond:
                while not self._streams[stream] and not pending:
                    await self._cond.wait()
                if self._streams[stream]:
                    msg_id, event = self._streams[stream].popleft()
                    pending[msg_id] = event
                else:
                    # Nothing new: redeliver the oldest un-acked entry, then move
                    # it to the back so a stuck message cannot monopolise the
                    # redelivery path — the same fairness an XAUTOCLAIM cursor
                    # sweep gets from advancing through the PEL.
                    msg_id, event = next(iter(pending.items()))
                    pending[msg_id] = pending.pop(msg_id)
            # Yield to the loop so a permanently-failing handler cannot starve
            # other tasks by spinning this redelivery path.
            await asyncio.sleep(0)
            yield DeliveredEvent(stream=stream, message_id=msg_id, event=event)

    async def ack(self, delivered: DeliveredEvent, *, group: str) -> None:
        self._pending[(delivered.stream, group)].pop(delivered.message_id, None)

    async def to_dlq(self, delivered: DeliveredEvent, *, reason: str) -> None:
        dlq = dlq_name(delivered.event.tenant_id, delivered.event.department)
        self._dlq[dlq].append((delivered.message_id, delivered.event, reason))

    # -- test/inspection helpers -------------------------------------------
    def published_of_type(self, type_: str) -> list[BaseEvent]:
        return [e for e in self._published if e.type == type_]

    @property
    def dlq(self) -> dict[str, list[tuple[str, BaseEvent, str]]]:
        return self._dlq
