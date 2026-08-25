"""Batch-level decode poison pill on REAL Postgres + REAL Redis.

The pool's jsonb codec runs ``json.loads`` INSIDE ``conn.fetch``
(``dal/connection.py:39-45``), so a document Postgres accepts but this
interpreter cannot decode kills the WHOLE batch fetch — before any row has been
identified — with a bare ``RecursionError`` that carries no row id. Because the
batch is ordered by ``created_at ASC``, ``run()``'s blanket handler then re-polls
the same first-in-line row forever and no decision event ever leaves the outbox.

That is the defect this suite pins. It is distinct from the shape guard in
``test_outbox_poison_pill_pg.py``: there the payload DECODES fine and is merely
not an object, so ``_publish_row`` already holds the row id. Here the failure is
one layer earlier, at the codec seam, with no row identity at all.

REAL PAYLOAD, NOT A SIMULATION. The row inserted below is written through real
asyncpg with a literal ``::jsonb`` cast, so Postgres itself validates and stores
it; the decode failure is then the genuine article. Both limits are stack-based
and differ per server/interpreter, so the depth is DISCOVERED at run time rather
than hard-coded: the test measures the shallowest depth this interpreter refuses
and checks that this Postgres accepts it (on PostgreSQL 16 with
``max_stack_depth=2MB`` and CPython 3.14 the storable-and-undecodable window is
roughly 14.3k-16.4k levels). If the two windows do not overlap on the machine at
hand, the test SKIPS with that stated reason rather than pretending.

Skipped unless SKYLIZE_TEST_DB_URL and SKYLIZE_TEST_REDIS_URL are set.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import asyncpg
import pytest
import pytest_asyncio

from skylize.dal.connection import Database

# A TEST may import decision_engine; only the live request path may not (K3).
from skylize.decision_engine.outbox_poller import OutboxPoller

from .conftest import DB_URL, REDIS_URL, requires_pg, requires_redis

pytestmark = pytest.mark.integration

_STREAM_SUFFIX = "decision"

#: Upper bound for the depth search. Well past any depth either side accepts.
_MAX_PROBE_DEPTH = 200_000


def _org() -> str:
    return f"decode_{uuid.uuid4().hex[:8]}"


def _nested(depth: int) -> str:
    """A JSON object nested ``depth`` levels: {"a":{"a":{...{"b":1}...}}}."""
    return "{" + '"a":{' * depth + '"b":1' + "}" * depth + "}"


def _undecodable_depth() -> int | None:
    """Shallowest nesting depth at which THIS interpreter's json.loads gives up.

    Measured in-process and in the same thread the decode will later fail on, so
    the number reflects the stack actually available. CPython raises a
    recoverable RecursionError for this rather than crashing, so bisecting is
    safe. Returns None if nothing up to the probe ceiling fails.
    """
    if not _fails_to_decode(_MAX_PROBE_DEPTH):
        return None
    lo, hi = 1, _MAX_PROBE_DEPTH
    while lo < hi:
        mid = (lo + hi) // 2
        if _fails_to_decode(mid):
            hi = mid
        else:
            lo = mid + 1
    return lo


def _fails_to_decode(depth: int) -> bool:
    try:
        json.loads(_nested(depth))
    except RecursionError:
        return True
    return False


@pytest_asyncio.fixture()
async def admin_db(migrated_public: None) -> AsyncIterator[Database]:
    """A ``Database`` as the admin role — how the relay's service role connects."""
    if not DB_URL:
        pytest.skip("SKYLIZE_TEST_DB_URL not set")
    db = Database(DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


@pytest_asyncio.fixture()
async def real_redis() -> AsyncIterator[Any]:
    if not REDIS_URL:
        pytest.skip("SKYLIZE_TEST_REDIS_URL not set")
    import redis.asyncio as aioredis

    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


async def _seed_tenant(admin_conn: Any, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _insert_outbox_row(
    admin_conn: Any, org: str, *, payload_json: str, created_at: datetime, row_key: str
) -> uuid.UUID:
    row_id = uuid.uuid4()
    await admin_conn.execute(
        """
        INSERT INTO decision_outbox (
            id, tenant_id, stream_key, event_type, payload, outbox_row_id,
            created_at, retry_count
        ) VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,0)
        """,
        row_id, org, f"evt:{org}:{_STREAM_SUFFIX}", "decision.approved",
        payload_json, row_key, created_at,
    )
    return row_id


async def _row_state(admin_conn: Any, row_id: uuid.UUID) -> dict:
    rec = await admin_conn.fetchrow(
        "SELECT published_at, failed_at, retry_count FROM decision_outbox WHERE id=$1",
        row_id,
    )
    return dict(rec)


async def _cleanup(admin_conn: Any, org: str) -> None:
    for sql in (
        "DELETE FROM decision_outbox WHERE tenant_id=$1",
        "DELETE FROM tenants WHERE org_id=$1",
    ):
        try:
            await admin_conn.execute(sql, org)
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass


async def _insert_undecodable(
    admin_conn: Any, org: str, *, created_at: datetime, row_key: str
) -> uuid.UUID:
    """Insert a row Postgres stores but json.loads cannot decode, or skip."""
    depth = _undecodable_depth()
    if depth is None:
        pytest.skip(
            f"no nesting up to {_MAX_PROBE_DEPTH} defeats this interpreter's json.loads"
        )
    try:
        return await _insert_outbox_row(
            admin_conn, org,
            payload_json=_nested(depth), created_at=created_at, row_key=row_key,
        )
    except asyncpg.PostgresError as exc:
        pytest.skip(
            f"this server will not store a {depth}-deep document "
            f"({type(exc).__name__}), and {depth} is the shallowest this "
            "interpreter cannot decode: the two windows do not overlap here"
        )


# ---------------------------------------------------------------------------
# The defect: the exception really does escape the fetch with no row identity
# ---------------------------------------------------------------------------

@requires_pg
async def test_the_batch_fetch_itself_raises_before_any_row_is_identified(
    admin_db, admin_conn
) -> None:
    """The premise, proven rather than assumed.

    The codec decodes inside ``conn.fetch``, so the failure is not attributable
    to a row — while the same batch WITHOUT the payload column reads cleanly and
    yields exactly the ids the fallback needs.
    """
    org = _org()
    try:
        await _seed_tenant(admin_conn, org)
        await _insert_undecodable(
            admin_conn, org,
            created_at=datetime.now(timezone.utc), row_key=f"{uuid.uuid4().hex[:13]}-prem",
        )

        from skylize.decision_engine.outbox_poller import (
            _BATCH_KEYS_SELECT,
            _BATCH_SELECT,
        )

        with pytest.raises(RecursionError):
            async with admin_db.admin_session() as conn:
                await conn.fetch(_BATCH_SELECT, 3, 50)

        # Same predicate, no payload column: decodes fine and names the row.
        async with admin_db.admin_session() as conn:
            keys = await conn.fetch(_BATCH_KEYS_SELECT, 3, 50)
        assert len(keys) == 1
        assert keys[0]["id"] is not None
    finally:
        await _cleanup(admin_conn, org)


# ---------------------------------------------------------------------------
# The fix: one poisoned row is condemned, the valid rows behind it publish
# ---------------------------------------------------------------------------

@requires_pg
@requires_redis
async def test_undecodable_row_marked_failed_and_valid_rows_still_publish(
    admin_db, admin_conn, real_redis, caplog
) -> None:
    """Two valid rows publish; the undecodable one is stamped failed.

    The poison row carries the EARLIEST created_at, so ``ORDER BY created_at
    ASC`` puts it first — the position from which it blocked everything behind
    it forever.
    """
    org = _org()
    base = datetime.now(timezone.utc) - timedelta(minutes=5)
    stream = f"evt:{org}:{_STREAM_SUFFIX}"
    try:
        await _seed_tenant(admin_conn, org)
        poison_id = await _insert_undecodable(
            admin_conn, org, created_at=base, row_key=f"{uuid.uuid4().hex[:13]}-0001"
        )
        good_ids = [
            await _insert_outbox_row(
                admin_conn, org,
                payload_json=json.dumps({"event_id": f"e{n}", "payload": {"decision_id": f"d{n}"}}),
                created_at=base + timedelta(seconds=n),
                row_key=f"{uuid.uuid4().hex[:13]}-000{n + 1}",
            )
            for n in (1, 2)
        ]

        poller = OutboxPoller(db=admin_db, redis=real_redis, settings=MagicMock())
        with caplog.at_level(logging.ERROR, logger="skylize.decision_engine.outbox_poller"):
            await poller._poll_and_publish()

        # The poisoned row is settled as failed, retry count preserved.
        poison = await _row_state(admin_conn, poison_id)
        assert poison["failed_at"] is not None, "undecodable row was not stamped failed"
        assert poison["published_at"] is None, "it must never be marked published"
        assert poison["retry_count"] == 0

        # BOTH valid rows published — the batch was not lost with the bad row.
        for good_id in good_ids:
            good = await _row_state(admin_conn, good_id)
            assert good["published_at"] is not None, "a valid row behind the poison never published"
            assert good["failed_at"] is None

        # ...and their events really reached the real stream, those two alone.
        entries = await real_redis.xrange(stream)
        assert len(entries) == 2
        assert sorted(fields["event_id"] for _id, fields in entries) == ["e1", "e2"]

        # ERROR logged, naming the row.
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any(
            r.getMessage() == "outbox_row_undecodable"
            and getattr(r, "outbox_row_id", None) is not None
            for r in errors
        ), f"no ERROR named the undecodable row: {[r.getMessage() for r in errors]}"

        # A second poll fetches nothing: the poisoned row left the batch
        # predicate instead of being served first forever.
        await poller._poll_and_publish()
        assert len(await real_redis.xrange(stream)) == 2
        remaining = await admin_conn.fetchval(
            "SELECT COUNT(*) FROM decision_outbox WHERE tenant_id=$1 "
            "AND published_at IS NULL AND failed_at IS NULL",
            org,
        )
        assert remaining == 0
    finally:
        await _cleanup(admin_conn, org)


# ---------------------------------------------------------------------------
# The classification: a dropped connection is NOT a poisoned row
# ---------------------------------------------------------------------------

@requires_pg
@requires_redis
async def test_connection_failure_does_not_condemn_any_row(
    admin_db, admin_conn, real_redis
) -> None:
    """A transport failure must propagate and leave the batch untouched.

    Condemning a row because the connection dropped would destroy a decision
    event nothing was ever wrong with. The fallback engages only for the decode
    allowlist, so this raises straight out of _poll_and_publish exactly as it did
    before the fallback existed — and nothing is stamped failed.
    """
    org = _org()
    try:
        await _seed_tenant(admin_conn, org)
        row_id = await _insert_outbox_row(
            admin_conn, org,
            payload_json=json.dumps({"event_id": "e1"}),
            created_at=datetime.now(timezone.utc),
            row_key=f"{uuid.uuid4().hex[:13]}-conn",
        )

        class _DroppedConnection:
            """A Database whose batch fetch fails the way a dropped link does."""

            def admin_session(self_inner):  # noqa: N805
                from contextlib import asynccontextmanager

                @asynccontextmanager
                async def _cm():
                    class _Conn:
                        async def fetch(self, *args: object) -> None:
                            raise asyncpg.exceptions.ConnectionDoesNotExistError(
                                "connection was closed in the middle of operation"
                            )

                    yield _Conn()

                return _cm()

        poller = OutboxPoller(
            db=_DroppedConnection(), redis=real_redis, settings=MagicMock()  # type: ignore[arg-type]
        )
        with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
            await poller._poll_and_publish()

        # Untouched: still pending, still retryable, nothing published.
        state = await _row_state(admin_conn, row_id)
        assert state["failed_at"] is None, "a dropped connection condemned a row"
        assert state["published_at"] is None
        assert state["retry_count"] == 0
        assert await real_redis.xrange(f"evt:{org}:{_STREAM_SUFFIX}") == []
    finally:
        await _cleanup(admin_conn, org)


@requires_pg
@requires_redis
async def test_healthy_batch_takes_the_single_fetch_path(
    admin_db, admin_conn, real_redis
) -> None:
    """The common path is unchanged: one batch fetch, no fallback, all published."""
    org = _org()
    base = datetime.now(timezone.utc)
    try:
        await _seed_tenant(admin_conn, org)
        row_ids = [
            await _insert_outbox_row(
                admin_conn, org,
                payload_json=json.dumps({"event_id": f"h{n}"}),
                created_at=base + timedelta(seconds=n),
                row_key=f"{uuid.uuid4().hex[:13]}-h{n}",
            )
            for n in (1, 2, 3)
        ]

        poller = OutboxPoller(db=admin_db, redis=real_redis, settings=MagicMock())
        calls: list[str] = []
        original = poller._publish_batch_row_by_row

        async def _spy() -> None:
            calls.append("fallback")
            await original()

        poller._publish_batch_row_by_row = _spy  # type: ignore[method-assign]
        await poller._poll_and_publish()

        assert calls == [], "the fallback engaged on a healthy batch"
        for row_id in row_ids:
            assert (await _row_state(admin_conn, row_id))["published_at"] is not None
        assert len(await real_redis.xrange(f"evt:{org}:{_STREAM_SUFFIX}")) == 3
    finally:
        await _cleanup(admin_conn, org)
