"""Decision Engine Redis Streams consumer.

Implements at-least-once delivery with idempotency, retry tracking,
XAUTOCLAIM-based reclaim of stuck messages, and DLQ routing.
Architecture: event_driven_architecture.md §7, §9.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, cast

import redis.asyncio as aioredis
from pydantic import ValidationError

from ..schemas.base import BaseEvent
from ..schemas.events import EVENT_REGISTRY
from .config import DecisionEngineSettings
from .constants import SUBSCRIBED_STREAMS
from .exceptions import DecisionEngineError
from .models import DecisionContext, DecisionResult

log = logging.getLogger(__name__)

# Single JSON field used by RedisEventBus; fallback to flat dict if absent.
_EVENT_FIELD = "event"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_fields(fields: dict[str, str]) -> BaseEvent | None:
    """Decode a stream entry's fields dict into a typed BaseEvent.

    Tries the single-field JSON envelope first (RedisEventBus format), then
    falls back to treating the flat fields dict as the event payload.
    """
    raw = fields.get(_EVENT_FIELD)
    if raw is not None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
    else:
        data = dict(fields)

    event_type = data.get("type")
    if not event_type:
        return None
    model = EVENT_REGISTRY.get(event_type)
    if model is None:
        return None
    try:
        return model.model_validate(data)
    except ValidationError:
        return None


class DecisionEngineConsumer:
    def __init__(
        self,
        redis: aioredis.Redis,
        settings: DecisionEngineSettings,
        pipeline_fn: Callable[[DecisionContext], Awaitable[DecisionResult]],
    ) -> None:
        self._redis = redis
        self._settings = settings
        self._pipeline_fn = pipeline_fn
        self._group = settings.redis_consumer_group
        self._consumer = settings.redis_consumer_name

    async def ensure_consumer_group(self) -> None:
        """Create consumer groups for all subscribed streams, idempotently."""
        for stream in SUBSCRIBED_STREAMS:
            try:
                await self._redis.xgroup_create(stream, self._group, id="$", mkstream=True)
                log.info("consumer_group_created", extra={"stream": stream, "group": self._group})
            except aioredis.ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise

    async def run(self) -> None:
        """Main loop: poll + reclaim idle until cancelled."""
        log.info("decision_engine_consumer_starting", extra={"consumer": self._consumer})
        try:
            while True:
                try:
                    await self._poll()
                    await self._reclaim_idle()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception(
                        "decision_engine_consumer_error_sleeping",
                        extra={"sleep_seconds": 5},
                    )
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            log.info("decision_engine_consumer_shutdown", extra={"consumer": self._consumer})

    async def _poll(self) -> None:
        """XREADGROUP > to claim new messages from all subscribed streams."""
        streams_arg: dict[str, str] = {s: ">" for s in SUBSCRIBED_STREAMS}
        resp = cast(
            list[tuple[str, list[tuple[str, dict[str, str]]]]],
            await self._redis.xreadgroup(
                self._group,
                self._consumer,
                streams_arg,
                count=self._settings.redis_batch_size,
                block=2000,
            ),
        )
        if not resp:
            return
        for stream, entries in resp:
            for msg_id, fields in entries:
                await self._process_message(stream, msg_id, fields)

    async def _process_message(
        self,
        stream: str,
        msg_id: str,
        fields: dict[str, str],
    ) -> None:
        """Deserialize, deduplicate, run pipeline, ack or retry/DLQ."""
        event = _decode_fields(fields)
        if event is None:
            log.warning(
                "decision_engine_schema_rejected",
                extra={"stream": stream, "msg_id": msg_id},
            )
            await self._redis.xack(stream, self._group, msg_id)
            await self._send_to_dlq(stream, msg_id, fields, ValueError("schema_rejected"))
            return

        event_id = str(event.event_id)

        # Idempotency: SETNX with 24 h TTL
        idempotency_key = f"decision_engine:processed:{event_id}"
        claimed = await self._redis.set(idempotency_key, 1, nx=True, ex=86400)
        if not claimed:
            log.debug(
                "decision_engine_duplicate_skipped",
                extra={"event_id": event_id, "stream": stream, "msg_id": msg_id},
            )
            await self._redis.xack(stream, self._group, msg_id)
            return

        context = DecisionContext(
            event_id=event_id,
            tenant_id=event.tenant_id,
            department=event.department,
            event_type=event.type,
            payload=event.model_dump(),
            received_at=datetime.now(timezone.utc),
        )

        try:
            await self._pipeline_fn(context)
            await self._redis.xack(stream, self._group, msg_id)
            log.info(
                "decision_engine_message_processed",
                extra={"event_id": event_id, "stream": stream},
            )
        except DecisionEngineError as exc:
            await self._handle_retry(stream, msg_id, fields, event_id, exc)
        except Exception as exc:
            await self._handle_retry(stream, msg_id, fields, event_id, exc)

    async def _handle_retry(
        self,
        stream: str,
        msg_id: str,
        fields: dict[str, str],
        event_id: str,
        error: Exception,
    ) -> None:
        retry_key = f"decision_engine:retries:{event_id}"
        count = await self._redis.hincrby(retry_key, "count", 1)
        await self._redis.expire(retry_key, 86400)
        log.warning(
            "decision_engine_processing_error",
            extra={
                "event_id": event_id,
                "stream": stream,
                "msg_id": msg_id,
                "error": str(error),
                "error_type": type(error).__name__,
                "retry_count": count,
                "max_retries": self._settings.redis_max_retries,
            },
        )
        if count >= self._settings.redis_max_retries:
            await self._send_to_dlq(stream, msg_id, fields, error)
        # else: do NOT ack — message stays in PEL for re-delivery

    async def _reclaim_idle(self) -> None:
        """XAUTOCLAIM messages stuck in dead consumers' PELs."""
        for stream in SUBSCRIBED_STREAMS:
            try:
                result = await self._redis.xautoclaim(
                    stream,
                    self._group,
                    self._consumer,
                    self._settings.redis_idle_time_ms,
                    start_id="0-0",
                    count=50,
                )
                # redis-py >= 4.3 returns (next_id, entries, deleted_ids)
                entries: list[tuple[str, dict[str, str]]]
                if isinstance(result, (list, tuple)) and len(result) >= 2:
                    entries = result[1]
                else:
                    continue
                for msg_id, fields in entries:
                    await self._process_message(stream, msg_id, fields)
            except aioredis.ResponseError:
                # XAUTOCLAIM unavailable on Redis < 6.2; skip silently.
                log.debug("xautoclaim_unavailable", extra={"stream": stream})

    async def _send_to_dlq(
        self,
        stream: str,
        msg_id: str,
        fields: dict[str, str],
        error: Exception,
    ) -> None:
        """Route a failed message to the DLQ stream and ACK it."""
        dlq_entry: dict[str, Any] = {
            "stream": stream,
            "msg_id": msg_id,
            "error": str(error),
            "error_type": type(error).__name__,
            "fields": json.dumps(fields),
            "failed_at": _utcnow(),
        }
        await self._redis.xadd(
            self._settings.redis_dlq_stream,
            cast("dict[Any, Any]", {k: str(v) for k, v in dlq_entry.items()}),
        )
        await self._redis.xack(stream, self._group, msg_id)
        log.error(
            "decision_engine_dlq_routed",
            extra={
                "stream": stream,
                "msg_id": msg_id,
                "dlq": self._settings.redis_dlq_stream,
                "error": str(error),
                "error_type": type(error).__name__,
            },
        )
