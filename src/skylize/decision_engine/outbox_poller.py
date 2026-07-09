"""Outbox poller — relays decision_outbox rows to Redis streams.

This is the ONLY component that reads decision_outbox without tenant RLS.
The poller runs as a system-level service (connecting as the service role,
not the skylize_app tenant-scoped role) because it must scan ALL tenants'
unpublished rows in a single query. RLS is bypassed by design here;
no other module does this.

Redis stream IDs must be monotonically increasing per stream.
outbox_row_id format ``{unix_ms}-{seq}`` satisfies this constraint as long
as the system clock does not go backward. If XADD returns a ResponseError
containing "ID specified is equal to or smaller than", a previous poller
instance already published the row — treat as idempotent success.
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

_MONOTONE_ID_ERROR = "ID specified is equal to or smaller than"


class OutboxPoller:
    """Polls decision_outbox and XADDs rows to Redis streams.

    RLS BYPASS NOTE: _poll_and_publish() runs via admin_session() (no
    skylize.org_id set), which means the RLS policy on decision_outbox does
    NOT apply. This is intentional — the poller is a system-level relay that
    must see all tenants' rows. No tenant data filtering happens here; the
    stream_key already encodes the tenant (``evt:{tenant_id}:decisions``).
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
        payload_str = row["payload"]

        # Deserialize payload — stored as JSONB string from asyncpg
        import json
        try:
            payload_dict = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
        except (ValueError, TypeError) as exc:
            log.error(
                "outbox_payload_deserialize_failed",
                extra={"outbox_row_id": row_id, "tenant_id": tenant_id, "error": str(exc)},
            )
            await self._mark_failed(db_id, row["retry_count"])
            return

        # Flatten payload for Redis stream fields (XADD expects flat key-value pairs)
        fields = {k: str(v) for k, v in _flatten_for_stream(payload_dict).items()}
        fields["event_type"] = row["event_type"]

        try:
            await self._redis.xadd(
                stream_key,
                fields,
                id=row_id,
            )
        except ResponseError as exc:
            err_msg = str(exc)
            if _MONOTONE_ID_ERROR in err_msg:
                # Previous poller instance already published this row — idempotent success.
                log.debug(
                    "outbox_row_already_published",
                    extra={"outbox_row_id": row_id, "tenant_id": tenant_id},
                )
                await self._mark_published(db_id)
                return

            log.warning(
                "outbox_xadd_redis_error",
                extra={"outbox_row_id": row_id, "tenant_id": tenant_id, "error": err_msg},
                exc_info=True,
            )
            new_retry = row["retry_count"] + 1
            if new_retry >= self.max_retry_count:
                log.error(
                    "outbox_row_max_retries_exceeded",
                    extra={"outbox_row_id": row_id, "tenant_id": tenant_id, "retry_count": new_retry},
                )
                await self._mark_failed(db_id, new_retry)
            else:
                await self._increment_retry(db_id, new_retry)
            return
        except Exception:
            log.warning(
                "outbox_xadd_unexpected_error",
                extra={"outbox_row_id": row_id, "tenant_id": tenant_id},
                exc_info=True,
            )
            new_retry = row["retry_count"] + 1
            if new_retry >= self.max_retry_count:
                log.error(
                    "outbox_row_max_retries_exceeded",
                    extra={"outbox_row_id": row_id, "tenant_id": tenant_id, "retry_count": new_retry},
                )
                await self._mark_failed(db_id, new_retry)
            else:
                await self._increment_retry(db_id, new_retry)
            return

        await self._mark_published(db_id)
        log.debug(
            "outbox_row_published",
            extra={"outbox_row_id": row_id, "tenant_id": tenant_id, "stream_key": stream_key},
        )

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
