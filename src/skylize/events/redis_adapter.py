"""
Redis Streams implementation of the EventBus port (event_driven_architecture.md §2).

One stream per department channel per tenant (`evt:{tenant}:{department}`),
consumer groups for at-least-once delivery, paired DLQ. Behind the EventBus port
so a future Kafka/NATS migration is an adapter swap.
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


class RedisEventBus:
    def __init__(self, url: str) -> None:
        self._client: redis.Redis = redis.from_url(url, decode_responses=True)

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
        while True:
            # decode_responses=True ⇒ all keys/values come back as str.
            resp = cast(
                list[tuple[str, list[tuple[str, dict[str, str]]]]],
                await self._client.xreadgroup(
                    group, consumer, {stream: ">"}, count=16, block=5000
                ),
            )
            if not resp:
                continue
            for _stream_key, entries in resp:
                for msg_id, fields in entries:
                    event = self._decode(fields)
                    if event is None:
                        await self._client.xack(stream, group, msg_id)
                        await self._raw_to_dlq(tenant_id, department, msg_id, fields,
                                               reason="schema_rejected")
                        continue
                    yield DeliveredEvent(stream=stream, message_id=msg_id, event=event)

    async def ack(self, delivered: DeliveredEvent, *, group: str) -> None:
        await self._client.xack(delivered.stream, group, delivered.message_id)

    async def to_dlq(self, delivered: DeliveredEvent, *, reason: str) -> None:
        dlq = dlq_name(delivered.event.tenant_id, delivered.event.department)
        await self._client.xadd(
            dlq,
            {_FIELD: delivered.event.model_dump_json(), "reason": reason},
        )

    # -- helpers ------------------------------------------------------------
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
