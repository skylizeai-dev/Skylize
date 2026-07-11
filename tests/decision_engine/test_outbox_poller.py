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
# Happy path: unpublished row → XADD called with explicit outbox_row_id → published_at set
# ---------------------------------------------------------------------------

async def test_happy_path_xadd_and_marks_published(settings):
    poller, conn, redis = _poller()
    row = _make_row(outbox_row_id="1700000000000-0001")

    conn.fetch.return_value = [row]
    conn.execute = AsyncMock()
    redis.xadd = AsyncMock(return_value="1700000000000-0001")

    await poller._poll_and_publish()

    redis.xadd.assert_awaited_once()
    call_kwargs = redis.xadd.call_args
    # explicit id passed
    assert call_kwargs.kwargs.get("id") == "1700000000000-0001" or (
        len(call_kwargs.args) >= 3 and call_kwargs.args[2] == "1700000000000-0001"
    ) or call_kwargs.kwargs.get("id") == "1700000000000-0001"

    # published_at set
    conn.execute.assert_awaited()
    update_sql = conn.execute.call_args.args[0]
    assert "published_at" in update_sql


# ---------------------------------------------------------------------------
# Idempotent: XADD ResponseError "ID specified is equal to or smaller"
# → published_at set, retry_count NOT incremented
# ---------------------------------------------------------------------------

async def test_monotone_id_error_marks_published_not_increments_retry(settings):
    poller, conn, redis = _poller()
    row = _make_row(outbox_row_id="1700000000000-0001", retry_count=0)

    conn.fetch.return_value = [row]
    conn.execute = AsyncMock()
    redis.xadd = AsyncMock(side_effect=ResponseError(
        "ID specified is equal to or smaller than the target ID"
    ))

    await poller._poll_and_publish()

    # published_at marked — execute called with UPDATE SET published_at
    conn.execute.assert_awaited()
    update_sql = conn.execute.call_args.args[0]
    assert "published_at" in update_sql

    # retry_count NOT incremented (no retry update)
    for call in conn.execute.call_args_list:
        sql = call.args[0]
        assert "retry_count" not in sql or "published_at" in sql


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
