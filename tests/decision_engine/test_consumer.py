"""Tests for DecisionEngineConsumer."""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest
import redis.asyncio as aioredis

from skylize.decision_engine.consumer import DecisionEngineConsumer
from skylize.decision_engine.exceptions import DecisionEngineError

from .conftest import make_decision_result


def _consumer(
    redis: AsyncMock,
    settings,
    pipeline_fn: AsyncMock | None = None,
) -> DecisionEngineConsumer:
    if pipeline_fn is None:
        pipeline_fn = AsyncMock(return_value=make_decision_result())
    return DecisionEngineConsumer(
        redis=redis,
        settings=settings,
        pipeline_fn=pipeline_fn,
    )


def _stream_entry(fields: dict[str, str], msg_id: str = "1234567890123-0001"):
    return (msg_id, fields)


def _valid_fields() -> dict[str, str]:
    eid = str(uuid.uuid4())
    return {
        "event": json.dumps({
            "type": "creative.review_requested",
            "event_id": eid,
            "tenant_id": "tenant-abc",
            "department": "creative",
            "partition_key": "brief:1",
            "correlation_id": str(uuid.uuid4()),
            "schema_version": "1.0",
            "category": "creative",
            "payload": {
                "brief_id": str(uuid.uuid4()),
                "asset_ids": [str(uuid.uuid4())],
                "proposed_action": "approve_internal",
                "proposed_spend_minor_units": None,
            },
        })
    }


# ---------------------------------------------------------------------------
# Happy path: valid event → pipeline called → XACK sent
# ---------------------------------------------------------------------------

async def test_happy_path_pipeline_called_and_acked(mock_redis, settings):
    pipeline_fn = AsyncMock(return_value=make_decision_result())
    consumer = _consumer(mock_redis, settings, pipeline_fn)

    fields = _valid_fields()
    msg_id = "1234567890123-0001"
    mock_redis.set.return_value = True  # setnx succeeds → new event

    await consumer._process_message("creative.review_requested", msg_id, fields)

    pipeline_fn.assert_awaited_once()
    mock_redis.xack.assert_awaited_once_with(
        "creative.review_requested", settings.redis_consumer_group, msg_id
    )


# ---------------------------------------------------------------------------
# Idempotent: duplicate event_id → pipeline NOT called → XACK sent
# ---------------------------------------------------------------------------

async def test_duplicate_event_not_processed_but_acked(mock_redis, settings):
    pipeline_fn = AsyncMock(return_value=make_decision_result())
    consumer = _consumer(mock_redis, settings, pipeline_fn)

    mock_redis.set.return_value = None  # setnx fails → duplicate

    fields = _valid_fields()
    msg_id = "1234567890123-0002"

    await consumer._process_message("creative.review_requested", msg_id, fields)

    pipeline_fn.assert_not_awaited()
    mock_redis.xack.assert_awaited_once_with(
        "creative.review_requested", settings.redis_consumer_group, msg_id
    )


# ---------------------------------------------------------------------------
# Retry: DecisionEngineError → retry counter incremented → no XACK
# ---------------------------------------------------------------------------

async def test_decision_engine_error_increments_retry_no_ack(mock_redis, settings):
    pipeline_fn = AsyncMock(side_effect=DecisionEngineError("boom"))
    consumer = _consumer(mock_redis, settings, pipeline_fn)

    mock_redis.set.return_value = True
    mock_redis.hincrby.return_value = 1  # first retry

    fields = _valid_fields()
    msg_id = "1234567890123-0003"

    await consumer._process_message("creative.review_requested", msg_id, fields)

    mock_redis.hincrby.assert_awaited_once()
    mock_redis.xack.assert_not_awaited()


# ---------------------------------------------------------------------------
# DLQ routing: retry > max_retries → XADD to DLQ → XACK sent
# ---------------------------------------------------------------------------

async def test_max_retries_routes_to_dlq_and_acks(mock_redis, settings):
    pipeline_fn = AsyncMock(side_effect=DecisionEngineError("permanent"))
    consumer = _consumer(mock_redis, settings, pipeline_fn)

    mock_redis.set.return_value = True
    mock_redis.hincrby.return_value = settings.redis_max_retries  # at threshold

    fields = _valid_fields()
    msg_id = "1234567890123-0004"

    await consumer._process_message("creative.review_requested", msg_id, fields)

    # DLQ XADD
    mock_redis.xadd.assert_awaited_once()
    xadd_call = mock_redis.xadd.call_args
    assert xadd_call.args[0] == settings.redis_dlq_stream

    # XACK after DLQ
    mock_redis.xack.assert_awaited_once_with(
        "creative.review_requested", settings.redis_consumer_group, msg_id
    )


# ---------------------------------------------------------------------------
# XAUTOCLAIM: idle message reclaimed → processed correctly
# ---------------------------------------------------------------------------

async def test_xautoclaim_reclaims_and_processes(mock_redis, settings):
    pipeline_fn = AsyncMock(return_value=make_decision_result())
    consumer = _consumer(mock_redis, settings, pipeline_fn)

    fields = _valid_fields()
    msg_id = "1234567890123-0005"
    # Return entry only for first stream, empty for the other two
    mock_redis.xautoclaim.side_effect = [
        ("0-0", [(msg_id, fields)], []),
        ("0-0", [], []),
        ("0-0", [], []),
    ]
    mock_redis.set.return_value = True

    await consumer._reclaim_idle()

    pipeline_fn.assert_awaited_once()
    mock_redis.xack.assert_awaited()


# ---------------------------------------------------------------------------
# Invalid schema → XACK + DLQ (schema_rejected path)
# ---------------------------------------------------------------------------

async def test_invalid_schema_xacks_and_dlqs(mock_redis, settings):
    consumer = _consumer(mock_redis, settings)

    bad_fields = {"event": '{"type": "unknown.event_type"}'}
    msg_id = "1234567890123-0006"

    await consumer._process_message("creative.review_requested", msg_id, bad_fields)

    # xack called at least once (schema_rejected path + _send_to_dlq also xacks)
    assert mock_redis.xack.await_count >= 1
    # DLQ xadd called
    mock_redis.xadd.assert_awaited_once()


# ---------------------------------------------------------------------------
# Non-DecisionEngineError also increments retry
# ---------------------------------------------------------------------------

async def test_generic_exception_increments_retry(mock_redis, settings):
    pipeline_fn = AsyncMock(side_effect=RuntimeError("db down"))
    consumer = _consumer(mock_redis, settings, pipeline_fn)

    mock_redis.set.return_value = True
    mock_redis.hincrby.return_value = 1

    fields = _valid_fields()
    await consumer._process_message("creative.review_requested", "msg-7", fields)

    mock_redis.hincrby.assert_awaited_once()
    mock_redis.xack.assert_not_awaited()


# ---------------------------------------------------------------------------
# ensure_consumer_group creates groups idempotently (BUSYGROUP ignored)
# ---------------------------------------------------------------------------

async def test_ensure_consumer_group_idempotent(mock_redis, settings):
    consumer = _consumer(mock_redis, settings)
    mock_redis.xgroup_create.side_effect = aioredis.ResponseError("BUSYGROUP already exists")

    # Should not raise
    await consumer.ensure_consumer_group()


async def test_ensure_consumer_group_raises_non_busygroup(mock_redis, settings):
    consumer = _consumer(mock_redis, settings)
    mock_redis.xgroup_create.side_effect = aioredis.ResponseError("ERR something else")

    with pytest.raises(aioredis.ResponseError):
        await consumer.ensure_consumer_group()
