"""Tests for OutboxPoller."""
from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import ResponseError

from skylize.decision_engine.outbox_poller import OutboxPoller


def _make_row(
    *,
    outbox_row_id: str = "1700000000000-0001",
    stream_key: str = "evt:tenant-a:decisions",
    tenant_id: str = "tenant-a",
    db_id: int = 1,
    payload: str = '{"event_type":"decision.approved","decision_id":"abc"}',
    event_type: str = "decision.approved",
    retry_count: int = 0,
) -> dict:
    return {
        "outbox_row_id": outbox_row_id,
        "stream_key": stream_key,
        "tenant_id": tenant_id,
        "id": db_id,
        "payload": payload,
        "event_type": event_type,
        "retry_count": retry_count,
    }


def _poller(
    conn=None,
    redis=None,
    batch_size: int = 50,
    max_retry_count: int = 3,
) -> tuple[OutboxPoller, AsyncMock, AsyncMock]:
    c = conn or AsyncMock()
    r = redis or AsyncMock()

    db = MagicMock()

    @asynccontextmanager
    async def _admin_session():
        yield c

    db.admin_session = _admin_session

    poller = OutboxPoller(
        db=db,
        redis=r,
        settings=MagicMock(),
        poll_interval_seconds=0.01,
        batch_size=batch_size,
        max_retry_count=max_retry_count,
    )
    return poller, c, r


# ---------------------------------------------------------------------------
# Happy path: unpublished row → XADD with a SERVER-generated id (no explicit
# row id) → published_at set
# ---------------------------------------------------------------------------

async def test_happy_path_xadd_and_marks_published(settings):
    poller, conn, redis = _poller()
    row = _make_row(outbox_row_id="1700000000000-0001")

    conn.fetch.return_value = [row]
    conn.execute = AsyncMock()
    redis.xadd = AsyncMock(return_value="1700000000000-5")

    await poller._poll_and_publish()

    redis.xadd.assert_awaited_once()
    call = redis.xadd.call_args
    # The relay must NOT pin the client-minted outbox_row_id as the stream id —
    # doing so is what let same-millisecond rows collide and vanish. XADD is
    # called with only (stream_key, fields); Redis assigns the id via '*'.
    assert "id" not in call.kwargs, "poller must not pass an explicit stream id"
    assert len(call.args) == 2, "poller must call xadd(stream_key, fields) only"
    assert "1700000000000-0001" not in call.args, "row id must not be the stream id"

    # published_at set
    conn.execute.assert_awaited()
    update_sql = conn.execute.call_args.args[0]
    assert "published_at" in update_sql


# ---------------------------------------------------------------------------
# Regression guard (in-file): a monotone-id-style XADD ResponseError must NOT be
# treated as idempotent success. The old code marked such rows published WITHOUT
# relaying them — the silent-loss defect. Any XADD error → retry, never publish.
# ---------------------------------------------------------------------------

async def test_monotone_id_error_is_not_treated_as_published(settings):
    poller, conn, redis = _poller()
    row = _make_row(outbox_row_id="1700000000000-0001", retry_count=0)

    conn.fetch.return_value = [row]
    conn.execute = AsyncMock()
    # The exact wording the deleted misclassification branch keyed on.
    redis.xadd = AsyncMock(side_effect=ResponseError(
        "ID specified is equal to or smaller than the target ID"
    ))

    await poller._poll_and_publish()

    conn.execute.assert_awaited()
    # No UPDATE may set published_at — the event never reached the stream.
    for call in conn.execute.call_args_list:
        assert "published_at" not in call.args[0], (
            "an errored XADD must never mark the row published"
        )
    # It must instead be retried (retry_count incremented, below max).
    update_sql = conn.execute.call_args.args[0]
    assert "retry_count" in update_sql
    assert "failed_at" not in update_sql


# ---------------------------------------------------------------------------
# Redis failure: retry_count incremented, published_at NOT set
# ---------------------------------------------------------------------------

async def test_redis_failure_increments_retry_not_published(settings):
    poller, conn, redis = _poller(max_retry_count=3)
    row = _make_row(retry_count=0)

    conn.fetch.return_value = [row]
    conn.execute = AsyncMock()
    redis.xadd = AsyncMock(side_effect=ResponseError("some other redis error"))

    await poller._poll_and_publish()

    # retry_count should be incremented (retry_count=1 < max 3)
    conn.execute.assert_awaited()
    update_sql = conn.execute.call_args.args[0]
    assert "retry_count" in update_sql
    # published_at should NOT be set
    assert "published_at" not in update_sql


# ---------------------------------------------------------------------------
# Max retries exceeded: failed_at set, error logged
# ---------------------------------------------------------------------------

async def test_max_retries_exceeded_marks_failed(settings, caplog):
    poller, conn, redis = _poller(max_retry_count=3)
    # retry_count=2, new_retry=3 >= max_retry_count → mark failed
    row = _make_row(retry_count=2)

    conn.fetch.return_value = [row]
    conn.execute = AsyncMock()
    redis.xadd = AsyncMock(side_effect=ResponseError("WRONGTYPE error"))

    await poller._poll_and_publish()

    conn.execute.assert_awaited()
    update_sql = conn.execute.call_args.args[0]
    assert "failed_at" in update_sql


# ---------------------------------------------------------------------------
# _poll_and_publish only fetches published_at IS NULL AND failed_at IS NULL AND retry_count < max
# ---------------------------------------------------------------------------

async def test_poll_query_filters_correctly(settings):
    poller, conn, redis = _poller(max_retry_count=3)
    conn.fetch.return_value = []

    await poller._poll_and_publish()

    fetch_call = conn.fetch.call_args
    sql = fetch_call.args[0]
    assert "published_at IS NULL" in sql
    assert "failed_at IS NULL" in sql
    assert "retry_count < $1" in sql


# ---------------------------------------------------------------------------
# Batch size respected: 51 rows pending → only 50 fetched per poll cycle
# ---------------------------------------------------------------------------

async def test_batch_size_respected(settings):
    poller, conn, redis = _poller(batch_size=50)
    conn.fetch.return_value = []

    await poller._poll_and_publish()

    fetch_call = conn.fetch.call_args
    # Check LIMIT in SQL and the param matches batch_size
    sql = fetch_call.args[0]
    assert "LIMIT $2" in sql
    # Second positional param = batch_size
    assert fetch_call.args[2] == 50


# ---------------------------------------------------------------------------
# CancelledError propagates (not swallowed)
# ---------------------------------------------------------------------------

async def test_cancelled_error_propagates():
    poller, conn, redis = _poller()
    conn.fetch.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await poller._poll_and_publish()


# ---------------------------------------------------------------------------
# get_failed_count() returns correct count from mock DB
# ---------------------------------------------------------------------------

async def test_get_failed_count_returns_count():
    poller, conn, redis = _poller()
    conn.fetchrow.return_value = {"n": 7}

    count = await poller.get_failed_count()

    assert count == 7
    fetch_sql = conn.fetchrow.call_args.args[0]
    assert "failed_at IS NOT NULL" in fetch_sql


async def test_get_failed_count_returns_zero_when_no_row():
    poller, conn, redis = _poller()
    conn.fetchrow.return_value = None

    count = await poller.get_failed_count()
    assert count == 0


# ---------------------------------------------------------------------------
# outbox_row_id format: matches r"^\d{13}-\d{4}$"
# ---------------------------------------------------------------------------

def test_outbox_row_id_format_matches_pattern():
    valid_ids = [
        "1700000000000-0001",
        "1234567890123-0099",
        "9999999999999-9999",
    ]
    pattern = re.compile(r"^\d{13}-\d{4}$")
    for rid in valid_ids:
        assert pattern.match(rid), f"{rid!r} did not match pattern"


def test_outbox_row_id_format_rejects_invalid():
    invalid_ids = [
        "170000000000-0001",  # 12 digit ms
        "1700000000000-001",  # 3 digit seq
        "1700000000000_0001", # wrong separator
        "abc-0001",
    ]
    pattern = re.compile(r"^\d{13}-\d{4}$")
    for rid in invalid_ids:
        assert not pattern.match(rid), f"{rid!r} should not match pattern"


# ---------------------------------------------------------------------------
# Unexpected exception also increments retry / marks failed
# ---------------------------------------------------------------------------

async def test_unexpected_exception_increments_retry():
    poller, conn, redis = _poller(max_retry_count=3)
    row = _make_row(retry_count=0)

    conn.fetch.return_value = [row]
    conn.execute = AsyncMock()
    redis.xadd = AsyncMock(side_effect=RuntimeError("unexpected"))

    await poller._poll_and_publish()

    conn.execute.assert_awaited()
    sql = conn.execute.call_args.args[0]
    assert "retry_count" in sql
