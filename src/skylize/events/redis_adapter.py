"""
Redis Streams implementation of the EventBus port (event_driven_architecture.md §2).

One stream per department channel per tenant (`evt:{tenant}:{department}`),
consumer groups, paired DLQ. Behind the EventBus port so a future Kafka/NATS
migration is an adapter swap.

At-least-once, via PEL reclaim. Each `consume` pass runs XAUTOCLAIM over the
group's pending-entries list before the `">"` read, so a message left un-acked by
a failing handler — or stranded in the PEL of a worker that died — is redelivered
once it has been idle for `reclaim_min_idle_ms`. That is what makes the router's
retry budget countable and its DLQ branch reachable; consumers stay idempotent on
`event_id` because redelivery means a handler can legitimately see the same event
twice.

RECLAIM IS RATE-BOUNDED, NOT AGE-BOUNDED. XAUTOCLAIM takes `count`, so each pass
reclaims at most `reclaim_batch` entries and then returns to the `">"` read; a
large accumulated backlog drains steadily instead of arriving as one flood. There
is deliberately NO age ceiling — no "ignore entries older than X". Such a ceiling
would silently abandon decisions in the PEL, which is the exact failure this
adapter exists to prevent, and staleness is a policy question for the pipeline,
not a transport one. See the first-start note in event_driven_architecture.md §8.

`start_id` is a per-generator cursor: XAUTOCLAIM returns the next cursor to scan
from and `0-0` when the scan wraps, so a long PEL is swept in order rather than
re-scanning its head every pass.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, cast

import redis.asyncio as redis

from ..schemas.base import BaseEvent
from ..schemas.events import EVENT_REGISTRY
from .bus import DeliveredEvent, dlq_name, stream_name

_FIELD = "event"  # single JSON field per stream entry

# Matches DecisionEngineSettings.redis_idle_time_ms, which is the knob the
# decision-engine composition root passes in. Defaulted here rather than imported
# because `events` is foundation: it must not depend on `decision_engine`.
DEFAULT_RECLAIM_MIN_IDLE_MS = 60_000
DEFAULT_RECLAIM_BATCH = 16


class RedisEventBus:
    def __init__(
        self,
        url: str,
        *,
        reclaim_min_idle_ms: int = DEFAULT_RECLAIM_MIN_IDLE_MS,
        reclaim_batch: int = DEFAULT_RECLAIM_BATCH,
    ) -> None:
        self._client: redis.Redis = redis.from_url(url, decode_responses=True)
        self._reclaim_min_idle_ms = reclaim_min_idle_ms
        self._reclaim_batch = reclaim_batch

    async def close(self) -> None:
        await self._client.aclose()

    async def publish(self, event: BaseEvent) -> str:
        # Validate is implicit: `event` is already a typed BaseEvent instance.
        stream = stream_name(event.tenant_id, event.department)
        # decode_responses=True ⇒ XADD returns the str message id.
        return cast(str, await self._client.xadd(stream, {_FIELD: event.model_dump_json()}))

    async def ensure_group(self, *, tenant_id: str, department: str, group: str) -> None:
        stream = stream_name(tenant_id, department)
        try:
            await self._client.xgroup_create(stream, group, id="0", mkstream=True)
        except redis.ResponseError as exc:  # BUSYGROUP — already exists
            if "BUSYGROUP" not in str(exc):
                raise

    async def consume(
        self, *, tenant_id: str, department: str, group: str, consumer: str
    ) -> AsyncIterator[DeliveredEvent]:
        stream = stream_name(tenant_id, department)
        await self.ensure_group(tenant_id=tenant_id, department=department, group=group)
        # Per-generator XAUTOCLAIM cursor; `0-0` restarts the PEL scan from its head.
        reclaim_cursor = "0-0"
        while True:
            # Reclaim BEFORE the ">" read: a stalled message that has already been
            # delivered once is older work than anything newly published, and this
            # ordering is what makes the retry budget advance on a quiet stream.
            reclaimed, reclaim_cursor = await self._reclaim(
                stream, group, consumer, reclaim_cursor
            )
            for msg_id, fields in reclaimed:
                delivered = await self._admit(tenant_id, department, group, stream,
                                              msg_id, fields)
                if delivered is not None:
                    yield delivered

            # Don't sit on a 5s block while a backlog is still draining: if this
            # pass claimed anything there is probably more behind it, so poll for
            # new messages and loop straight back to the next reclaim batch.
            # Claiming resets an entry's idle clock, so a real (60s-window)
            # deployment cannot spin here — the same entry is not re-claimable
            # until the window elapses again.
            # NB `None`, not 0 — `BLOCK 0` means block FOREVER, not "don't block".
            block = None if reclaimed else 5000

            # decode_responses=True ⇒ all keys/values come back as str.
            resp = cast(
                list[tuple[str, list[tuple[str, dict[str, str]]]]],
                await self._client.xreadgroup(
                    group, consumer, {stream: ">"}, count=16, block=block
                ),
            )
            if not resp:
                continue
            for _stream_key, entries in resp:
                for msg_id, fields in entries:
                    delivered = await self._admit(tenant_id, department, group, stream,
                                                  msg_id, fields)
                    if delivered is not None:
                        yield delivered

    async def ack(self, delivered: DeliveredEvent, *, group: str) -> None:
        await self._client.xack(delivered.stream, group, delivered.message_id)

    async def to_dlq(self, delivered: DeliveredEvent, *, reason: str) -> None:
        dlq = dlq_name(delivered.event.tenant_id, delivered.event.department)
        await self._client.xadd(
            dlq,
            {_FIELD: delivered.event.model_dump_json(), "reason": reason},
        )

    # -- helpers ------------------------------------------------------------
    async def _reclaim(
        self, stream: str, group: str, consumer: str, cursor: str
    ) -> tuple[list[tuple[str, dict[str, str]]], str]:
        """Take ownership of up to `reclaim_batch` stalled PEL entries.

        Returns the claimed entries and the next scan cursor. Claiming from this
        consumer's OWN pending list is intentional and is the single-worker retry
        path: a handler that raised leaves its message pending, and after the idle
        window the same worker reclaims and re-delivers it.

        Entries whose stream message was trimmed or deleted come back with no
        fields; they are acked away rather than yielded, since there is nothing
        left to decode and leaving them would block the PEL scan forever.
        """
        try:
            reply = cast(
                "tuple[Any, ...]",
                await self._client.xautoclaim(
                    stream,
                    group,
                    consumer,
                    min_idle_time=self._reclaim_min_idle_ms,
                    start_id=cursor,
                    count=self._reclaim_batch,
                ),
            )
        except redis.ResponseError:
            # NOGROUP: the stream or group was dropped underneath us. The next
            # loop's ensure_group/xreadgroup re-establishes it; nothing to claim.
            return [], "0-0"

        # Redis >= 7.0 replies (cursor, entries, deleted); 6.2 omits the third
        # element. Unpacking a fixed arity would kill this generator — and with it
        # the delivery guarantee — against a managed 6.2 instance, so read
        # positionally. infra pins redis:7-alpine; this is for anything that isn't.
        cursor_out: str = reply[0]
        entries = cast("list[tuple[str, dict[str, str] | None]]", reply[1])

        live = [(msg_id, fields) for msg_id, fields in entries if fields]
        for msg_id, fields in entries:
            if not fields:
                await self._client.xack(stream, group, msg_id)
        return live, cursor_out

    async def _admit(
        self,
        tenant_id: str,
        department: str,
        group: str,
        stream: str,
        msg_id: str,
        fields: dict[str, str],
    ) -> DeliveredEvent | None:
        """Decode one entry, or ack-and-DLQ it if the schema does not resolve."""
        event = self._decode(fields)
        if event is None:
            await self._client.xack(stream, group, msg_id)
            await self._raw_to_dlq(tenant_id, department, msg_id, fields,
                                   reason="schema_rejected")
            return None
        return DeliveredEvent(stream=stream, message_id=msg_id, event=event)

    @staticmethod
    def _decode(fields: dict[str, str]) -> BaseEvent | None:
        raw = fields.get(_FIELD)
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            model = EVENT_REGISTRY.get(data.get("type"))
            if model is None:
                return None
            return model.model_validate(data)
        except Exception:
            return None

    async def _raw_to_dlq(
        self, tenant_id: str, department: str, msg_id: str, fields: dict[str, str], *, reason: str
    ) -> None:
        entry: dict[str, str] = {**fields, "reason": reason, "orig_id": msg_id}
        # redis-py's xadd mapping type is intentionally broad; our str→str entry
        # satisfies it at runtime (cast narrows the stub's invariant param).
        await self._client.xadd(dlq_name(tenant_id, department), cast("dict[Any, Any]", entry))
