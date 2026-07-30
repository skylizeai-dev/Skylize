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
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from skylize.decision_engine.config import DecisionEngineSettings

if TYPE_CHECKING:
    from skylize.dal.connection import Database

log = logging.getLogger(__name__)


# The one batch predicate, written once so the id-only re-read below provably
# selects the SAME rows as the full read it is recovering from.
_BATCH_PREDICATE = """
    FROM decision_outbox
    WHERE published_at IS NULL
      AND failed_at IS NULL
      AND retry_count < $1
    ORDER BY created_at ASC
    LIMIT $2
"""

_BATCH_SELECT = (
    "SELECT id, tenant_id, stream_key, event_type, payload, outbox_row_id, retry_count"
    + _BATCH_PREDICATE
)

# Same batch, WITHOUT the payload column. Because the jsonb codec only runs for
# a jsonb column that is actually selected, this projection cannot hit the decode
# failure the full read hit — which is the whole reason it can name the row.
_BATCH_KEYS_SELECT = "SELECT id, retry_count, outbox_row_id, tenant_id" + _BATCH_PREDICATE

_SINGLE_ROW_SELECT = """
    SELECT id, tenant_id, stream_key, event_type, payload, outbox_row_id, retry_count
    FROM decision_outbox
    WHERE id = $1
      AND published_at IS NULL
      AND failed_at IS NULL
"""

# EXACTLY the exceptions the pool's jsonb decoder — `json.loads`
# (dal/connection.py:39-45) — can raise, and nothing else.
#
# This allowlist is deliberately narrow rather than a broad `ValueError`, because
# the classification carries real consequence: a DECODE failure means one
# specific row is permanently unrelayable and must be condemned, while a
# CONNECTION failure means the batch is fine and must be retried. Condemning a
# row because the connection dropped would destroy a decision event that nothing
# was ever wrong with.
#
# Everything not listed here propagates untouched, so the pre-existing behaviour
# (run() logs, sleeps, re-polls the same batch) is what a dropped connection
# still gets. That covers every asyncpg failure by construction: asyncpg raises
# PostgresError for server-side failures — including the connection ones,
# ConnectionDoesNotExistError and ConnectionFailureError, which derive from
# PostgresConnectionError -> PostgresError — and InterfaceError for
# protocol/client failures, plus bare OSError/TimeoutError from the transport.
# None of those is a subclass of any type below (verified against asyncpg's
# exception module: its only ValueError subclass is ClientConfigurationError,
# raised at connect time, and it is not a JSONDecodeError).
_DECODE_FAILURES: tuple[type[Exception], ...] = (
    # A document nested deeper than this interpreter can decode. Postgres stores
    # JSONB far deeper than json.loads will parse: on PostgreSQL 16 with
    # max_stack_depth=2MB and CPython 3.14, depths ~14.3k-16.4k are storable and
    # undecodable. This is the failure that reaches conn.fetch as a bare
    # RecursionError, before any row has been identified.
    RecursionError,
    # A JSONDecodeError cannot arise from a valid JSONB column today, but the
    # decoder can raise it and the disposition would be identical.
    json.JSONDecodeError,
    UnicodeDecodeError,
)


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
        """Fetch a batch of unpublished rows and relay each one to Redis.

        BATCH-LEVEL DECODE POISON PILL. The pool's jsonb codec runs `json.loads`
        INSIDE `conn.fetch` (dal/connection.py:39-45), i.e. before any row has
        been identified. A document Postgres accepts but this interpreter cannot
        decode therefore kills the WHOLE fetch with a bare RecursionError that
        carries no row id — and since the batch is ordered by created_at ASC,
        run()'s blanket handler re-polls the same first-in-line row forever and
        no decision event ever leaves the outbox again.

        The common path is unchanged: ONE batch fetch, then relay. Only when that
        fetch fails to DECODE does the per-row fallback engage, and only then.
        """
        try:
            async with self._db.admin_session() as conn:
                rows = await conn.fetch(
                    _BATCH_SELECT, self.max_retry_count, self.batch_size
                )
        except _DECODE_FAILURES:
            # A row in this batch is undecodable, but the exception cannot say
            # which. Everything else — a dropped connection above all — is NOT
            # caught here and keeps propagating, because retrying the batch is
            # the correct response to a transport failure.
            log.error("outbox_batch_decode_failed", exc_info=True)
            await self._publish_batch_row_by_row()
            return

        for row in rows:
            await self._publish_row(row)

    async def _publish_batch_row_by_row(self) -> None:
        """Identify and condemn the undecodable row(s); relay the rest.

        Re-reads the same batch WITHOUT the payload column — a projection that
        cannot engage the codec, so it cannot hit the same poison — which yields
        the row ids the failed fetch could not provide. Each row's payload is
        then read on its own: a row whose OWN read fails to decode is stamped
        failed with its retry count preserved (it is permanently unrelayable, not
        retryable) and logged at ERROR with its id; every other row publishes as
        usual.

        BOUND: one id-only query plus AT MOST ``batch_size`` single-row reads,
        once per _poll_and_publish call. There is no recursion and no retry loop
        inside this method — if the fallback itself fails (say the connection
        drops mid-way) the exception propagates to run(), which logs, sleeps, and
        re-polls. The fallback can therefore never spin.
        """
        async with self._db.admin_session() as conn:
            keys = await conn.fetch(
                _BATCH_KEYS_SELECT, self.max_retry_count, self.batch_size
            )
        log.error(
            "outbox_batch_decode_fallback_engaged",
            extra={"batch_rows": len(keys)},
        )

        for key in keys:
            db_id = key["id"]
            try:
                async with self._db.admin_session() as conn:
                    row = await conn.fetchrow(_SINGLE_ROW_SELECT, db_id)
            except _DECODE_FAILURES:
                # THE poisoned row, now named. Same disposition as the shape
                # guard in _publish_row: a document that cannot be decoded on
                # this poll cannot be decoded on any future poll either.
                log.error(
                    "outbox_row_undecodable",
                    extra={
                        "outbox_row_id": key["outbox_row_id"],
                        "tenant_id": key["tenant_id"],
                        "retry_count": key["retry_count"],
                    },
                    exc_info=True,
                )
                await self._mark_failed(db_id, key["retry_count"])
                continue
            if row is None:
                # Settled between the two reads (another poller, or a manual
                # intervention). Nothing to do; not an error.
                continue
            await self._publish_row(row)

    async def _publish_row(self, row: Any) -> None:
        row_id = row["outbox_row_id"]
        stream_key = row["stream_key"]
        tenant_id = row["tenant_id"]
        db_id = row["id"]
        # JSONB decodes via the pool codec (dal.connection._init_connection) to
        # whatever document is actually stored — a dict for a JSON object, but a
        # list / str / int / bool / None for any other VALID JSONB value. The
        # column constrains the row to valid JSONB, never to an object.
        payload = row["payload"]

        # SHAPE GUARD. Only a JSON object can become Redis stream fields; anything
        # else makes _flatten_for_stream raise AttributeError on `.items()`. That
        # exception escapes every except block below (they wrap the XADD only),
        # escapes _poll_and_publish (no handler), and lands in run()'s blanket
        # handler, which sleeps and re-polls — and since the batch query orders by
        # created_at ASC, the SAME row is fetched first forever and NO decision
        # event ever leaves the outbox again. A payload that cannot be relayed is
        # permanently unrelayable, so it is stamped failed (retry count preserved)
        # and skipped rather than retried. This guard predates the JSONB codec
        # change that removed it: the codec makes malformed JSON unrepresentable,
        # not non-object JSON.
        if not isinstance(payload, dict):
            log.error(
                "outbox_payload_not_an_object",
                extra={
                    "outbox_row_id": row_id,
                    "tenant_id": tenant_id,
                    "payload_type": type(payload).__name__,
                    "retry_count": row["retry_count"],
                },
            )
            await self._mark_failed(db_id, row["retry_count"])
            return

        # Flatten payload for Redis stream fields (XADD expects flat key-value
        # pairs). Same disposition as the shape guard for the same reason: a
        # document this cannot render (e.g. nesting deep enough to exhaust the
        # recursion limit) is unrelayable on every future poll, so failing it once
        # is the only way the poller makes progress.
        try:
            fields = {k: str(v) for k, v in _flatten_for_stream(payload).items()}
        except Exception:
            log.error(
                "outbox_payload_unflattenable",
                extra={
                    "outbox_row_id": row_id,
                    "tenant_id": tenant_id,
                    "retry_count": row["retry_count"],
                },
                exc_info=True,
            )
            await self._mark_failed(db_id, row["retry_count"])
            return
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
