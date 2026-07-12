"""Extra outbox_poller tests: run() loop, payload deserialize failure, dict payload."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import ResponseError

from skylize.decision_engine.outbox_poller import OutboxPoller, _flatten_for_stream


def _make_row(
    *,
    outbox_row_id: str = "1700000000000-0001",
    stream_key: str = "evt:t:decisions",
    tenant_id: str = "t",
    db_id: int = 1,
    payload = '{"key": "val"}',
    event_type: str = "decision.approved",
    retry_count: int = 0,
):
    return {
        "outbox_row_id": outbox_row_id,
        "stream_key": stream_key,
        "tenant_id": tenant_id,
        "id": db_id,
        "payload": payload,
        "event_type": event_type,
        "retry_count": retry_count,
    }


def _poller(conn=None, redis=None, max_retry_count=3, batch_size=50):
    c = conn or AsyncMock()
    r = redis or AsyncMock()
    db = MagicMock()

    @asynccontextmanager
    async def _admin_session():
        yield c

    db.admin_session = _admin_session
    p = OutboxPoller(
        db=db,
        redis=r,
        settings=MagicMock(),
        poll_interval_seconds=0.01,
        batch_size=batch_size,
        max_retry_count=max_retry_count,
    )
    return p, c, r


# ---------------------------------------------------------------------------
# run(): cancellation propagates
# ---------------------------------------------------------------------------

async def test_run_cancellation_propagates():
    poller, conn, redis = _poller()
    conn.fetch.return_value = []

    task = asyncio.create_task(poller.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# run(): exception in _poll_and_publish → sleeps and continues
# ---------------------------------------------------------------------------

async def test_run_recovers_from_exception():
    call_count = 0

    async def _flaky():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient")
        raise asyncio.CancelledError()

    poller, conn, redis = _poller()
    poller._poll_and_publish = _flaky

    with patch("skylize.decision_engine.outbox_poller.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(asyncio.CancelledError):
            await poller.run()

    assert call_count >= 2


# ---------------------------------------------------------------------------
# Payload deserialize failure → mark_failed called, xadd NOT called
# ---------------------------------------------------------------------------

async def test_payload_deserialize_failure_marks_failed():
    poller, conn, redis = _poller()
    row = _make_row(payload="not-valid-json-{{{")
    conn.fetch.return_value = [row]
    conn.execute = AsyncMock()

    await poller._poll_and_publish()

    # xadd should NOT have been called
    redis.xadd.assert_not_awaited()
    # mark_failed → UPDATE decision_outbox SET failed_at
    conn.execute.assert_awaited()
    sql = conn.execute.call_args.args[0]
    assert "failed_at" in sql


# ---------------------------------------------------------------------------
# Dict payload (asyncpg JSONB returns dict, not str) → processed normally
# ---------------------------------------------------------------------------

async def test_dict_payload_processed_without_json_parse(settings):
    poller, conn, redis = _poller()
    row = _make_row(payload={"already": "parsed", "amount": 100})
    conn.fetch.return_value = [row]
    conn.execute = AsyncMock()
    redis.xadd = AsyncMock(return_value="1234-0")

    await poller._poll_and_publish()

    redis.xadd.assert_awaited_once()


# ---------------------------------------------------------------------------
# _flatten_for_stream: nested dicts and lists
# ---------------------------------------------------------------------------

def test_flatten_for_stream_nested_dict():
    data = {"outer": {"inner": "val"}}
    flat = _flatten_for_stream(data)
    assert flat["outer.inner"] == "val"
    assert "outer.inner" in flat


def test_flatten_for_stream_list():
    data = {"items": [1, 2, 3]}
    flat = _flatten_for_stream(data)
    assert flat["items"] == "[1, 2, 3]"


def test_flatten_for_stream_primitive():
    flat = _flatten_for_stream({"n": 42, "s": "hello"})
    assert flat["n"] == 42
    assert flat["s"] == "hello"


# ---------------------------------------------------------------------------
# retry_count incremented when < max (ResponseError non-monotone)
# ---------------------------------------------------------------------------

async def test_redis_error_increments_retry_count_below_max():
    poller, conn, redis = _poller(max_retry_count=5)
    row = _make_row(retry_count=1)
    conn.fetch.return_value = [row]
    conn.execute = AsyncMock()
    redis.xadd = AsyncMock(side_effect=ResponseError("some other error"))

    await poller._poll_and_publish()

    conn.execute.assert_awaited()
    sql = conn.execute.call_args.args[0]
    assert "retry_count" in sql
    # Should be _increment_retry, not _mark_failed (new_retry=2 < 5)
    assert "failed_at" not in sql


# ---------------------------------------------------------------------------
# Multiple rows in batch — all processed
# ---------------------------------------------------------------------------

async def test_multiple_rows_all_processed():
    poller, conn, redis = _poller()
    rows = [_make_row(db_id=i, outbox_row_id=f"170000000000{i}-000{i}") for i in range(1, 4)]
    conn.fetch.return_value = rows
    conn.execute = AsyncMock()
    redis.xadd = AsyncMock(return_value="1234-0")

    await poller._poll_and_publish()

    assert redis.xadd.await_count == 3
    assert conn.execute.await_count == 3  # mark_published x3
