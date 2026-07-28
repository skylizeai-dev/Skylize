"""Outbox poller — relays decision_outbox rows to Redis streams.

This is the ONLY component that reads decision_outbox without tenant RLS.
The poller runs as a system-level service (connecting as the service role,
not the skylize_app tenant-scoped role) because it must scan ALL tenants'
unpublished rows in a single query. RLS is bypassed by design here;
no other module does this.

Stream IDs are SERVER-GENERATED. Each row is relayed with ``XADD <stream> *``,
so Redis assigns a strictly-increasing ``{ms}-{seq}`` id itself — monotonic by
construction, with no possibility of two rows colliding on an id. The row's
``outbox_row_id`` is NOT used as the stream id; it is only a unique row key.

Why not a client-minted id? An explicit ``{unix_ms}-{seq}`` id (the previous
scheme) collided whenever two rows were created in the same millisecond, and
XADD rejected the later one with "ID ... is equal or smaller than the target
stream top item". That error was being classified as idempotent success and the
row marked published WITHOUT ever reaching the stream — silent decision-event
loss. With server-generated ids the error cannot occur, so a row is marked
published ONLY after a successful XADD returns an id (proving the entry exists);
any XADD error means "not appended" → retry, never mark published.

At-least-once is preserved, not exactly-once: a crash between XADD and the
``published_at`` stamp re-relays the row on recovery (a second stream entry with
the SAME ``event_id``). That is the bus's documented contract — consumers of the
decision channel are idempotent on ``event_id`` (see events/bus.py) — and it
matches how the live inline engine already publishes here via RedisEventBus.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from skylize.decision_engine.config import DecisionEngineSettings

if TYPE_CHECKING:
    from skylize.dal.connection import Database

log = logging.getLogger(__name__)


class OutboxPoller:
    """Polls decision_outbox and XADDs rows to Redis streams.

    RLS BYPASS NOTE: _poll_and_publish() runs via admin_session() (no
    skylize.org_id set), which means the RLS policy on decision_outbox does
    NOT apply. This is intentional — the poller is a system-level relay that
    must see all tenants' rows. No tenant data filtering happens here; the
    stream_key already encodes the tenant (``evt:{tenant_id}:decision``).
    """

    def __init__(
        self,
        db: "Database",
        redis: aioredis.Redis,
        settings: DecisionEngineSettings,
        *,
        poll_interval_seconds: float = 0.5,
        batch_size: int = 50,
        max_retry_count: int = 3,
    ) -> None:
        self._db = db
        self._redis = redis
        self._settings = settings
        self.poll_interval_seconds = poll_interval_seconds
        self.batch_size = batch_size
        self.max_retry_count = max_retry_count

    async def run(self) -> None:
        """Infinite poll loop. Designed to run as an asyncio background task."""
        log.info("outbox_poller_started", extra={"poll_interval_seconds": self.poll_interval_seconds})
        while True:
            try:
                await self._poll_and_publish()
            except asyncio.CancelledError:
                log.info("outbox_poller_shutdown")
                raise
            except Exception:
                log.error("outbox_poller_unexpected_error", exc_info=True)
                await asyncio.sleep(5.0)
                continue
            await asyncio.sleep(self.poll_interval_seconds)

    async def _poll_and_publish(self) -> None:
        """Fetch a batch of unpublished rows and relay each one to Redis."""
        async with self._db.admin_session() as conn:
            rows = await conn.fetch(
                """
                SELECT id, tenant_id, stream_key, event_type, payload, outbox_row_id,
                       retry_count
                FROM decision_outbox
                WHERE published_at IS NULL
                  AND failed_at IS NULL
                  AND retry_count < $1
                ORDER BY created_at ASC
                LIMIT $2
                """,
                self.max_retry_count,
                self.batch_size,
            )

        for row in rows:
            await self._publish_row(row)

    async def _publish_row(self, row: Any) -> None:
        row_id = row["outbox_row_id"]
        stream_key = row["stream_key"]
        tenant_id = row["tenant_id"]
        db_id = row["id"]
        # JSONB decodes to dict via the pool codec (dal.connection._init_connection).
        payload_dict = row["payload"]

        # Flatten payload for Redis stream fields (XADD expects flat key-value pairs)
        fields = {k: str(v) for k, v in _flatten_for_stream(payload_dict).items()}
        fields["event_type"] = row["event_type"]

        # Server-generated id (``XADD <stream> *``): Redis assigns a
        # strictly-increasing id, so the relay is monotonic by construction and
        # two rows can never collide. row_id (``outbox_row_id``) is deliberately
        # NOT passed as the id — it is only a unique row key.
        try:
            stream_id = await self._redis.xadd(stream_key, fields)
        except ResponseError as exc:
            log.warning(
                "outbox_xadd_redis_error",
                extra={"outbox_row_id": row_id, "tenant_id": tenant_id, "error": str(exc)},
                exc_info=True,
            )
            await self._retry_or_fail(db_id, row["retry_count"], row_id=row_id, tenant_id=tenant_id)
            return
        except Exception:
            log.warning(
                "outbox_xadd_unexpected_error",
                extra={"outbox_row_id": row_id, "tenant_id": tenant_id},
                exc_info=True,
            )
            await self._retry_or_fail(db_id, row["retry_count"], row_id=row_id, tenant_id=tenant_id)
            return

        # A successful XADD returns the server-assigned id, which proves the entry
        # is on the stream. ONLY now is it safe to mark the row published — an
        # errored XADD (handled above) never reaches here, so a row is never
        # marked published without a verified stream entry.
        await self._mark_published(db_id)
        log.debug(
            "outbox_row_published",
            extra={
                "outbox_row_id": row_id,
                "tenant_id": tenant_id,
                "stream_key": stream_key,
                "stream_id": stream_id,
            },
        )

    async def _retry_or_fail(
        self, db_id: Any, current_retry: int, *, row_id: str, tenant_id: str
    ) -> None:
        """Increment the retry counter, or stamp ``failed_at`` once it is spent.

        The single settlement path for every XADD error. A row is NEVER marked
        published here: an event that did not reach the stream must be re-relayed
        (or, past the budget, left visible as failed), not silently settled. This
        is the invariant whose violation was the silent-loss defect.
        """
        new_retry = current_retry + 1
        if new_retry >= self.max_retry_count:
            log.error(
                "outbox_row_max_retries_exceeded",
                extra={"outbox_row_id": row_id, "tenant_id": tenant_id, "retry_count": new_retry},
            )
            await self._mark_failed(db_id, new_retry)
        else:
            await self._increment_retry(db_id, new_retry)

    async def _mark_published(self, db_id: Any) -> None:
        async with self._db.admin_session() as conn:
            await conn.execute(
                "UPDATE decision_outbox SET published_at = $1 WHERE id = $2",
                datetime.now(timezone.utc),
                db_id,
            )

    async def _mark_failed(self, db_id: Any, retry_count: int) -> None:
        async with self._db.admin_session() as conn:
            await conn.execute(
                """
                UPDATE decision_outbox
                SET failed_at = $1, retry_count = $2
                WHERE id = $3
                """,
                datetime.now(timezone.utc),
                retry_count,
                db_id,
            )

    async def _increment_retry(self, db_id: Any, new_retry_count: int) -> None:
        async with self._db.admin_session() as conn:
            await conn.execute(
                "UPDATE decision_outbox SET retry_count = $1 WHERE id = $2",
                new_retry_count,
                db_id,
            )

    async def get_failed_count(self) -> int:
        """Return count of permanently failed outbox rows. Used by health checks."""
        async with self._db.admin_session() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS n FROM decision_outbox WHERE failed_at IS NOT NULL"
            )
        return int(row["n"]) if row else 0


def _flatten_for_stream(payload: dict, prefix: str = "") -> dict[str, Any]:
    """Recursively flatten nested dict to dot-separated keys for Redis stream fields."""
    out: dict[str, Any] = {}
    for k, v in payload.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten_for_stream(v, full_key))
        elif isinstance(v, list):
            import json as _json
            out[full_key] = _json.dumps(v)
        else:
            out[full_key] = v
    return out
