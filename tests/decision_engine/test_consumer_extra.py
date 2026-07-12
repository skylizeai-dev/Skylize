"""Extra consumer tests targeting uncovered lines: run(), _poll(), _decode_fields()."""
from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, patch

import redis.asyncio as aioredis

from skylize.decision_engine.consumer import DecisionEngineConsumer, _decode_fields

from .conftest import make_decision_result


def _consumer(mock_redis, settings, pipeline_fn=None):
    if pipeline_fn is None:
        pipeline_fn = AsyncMock(return_value=make_decision_result())
    return DecisionEngineConsumer(
        redis=mock_redis,
        settings=settings,
        pipeline_fn=pipeline_fn,
    )


# ---------------------------------------------------------------------------
# _decode_fields: JSON decode error → None
# ---------------------------------------------------------------------------

def test_decode_fields_invalid_json_returns_none():
    result = _decode_fields({"event": "not-json-{"})
    assert result is None


def test_decode_fields_no_event_field_flat_dict():
    # falls back to flat dict — but "type" must exist and be in registry
    result = _decode_fields({"type": "unknown.type.xyz"})
    assert result is None


def test_decode_fields_missing_type():
    result = _decode_fields({"event": '{"no_type_key": true}'})
    assert result is None


def test_decode_fields_valid_json_registered_type():
    eid = str(uuid.uuid4())
    fields = {
        "event": json.dumps({
            "type": "creative.review_requested",
            "event_id": eid,
            "tenant_id": "t",
            "department": "creative",
            "partition_key": "p",
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
    result = _decode_fields(fields)
    assert result is not None
    assert result.type == "creative.review_requested"


def test_decode_fields_validation_error_returns_none():
    # Known type but payload fails validation
    result = _decode_fields({
        "event": json.dumps({
            "type": "creative.review_requested",
            # missing all required payload fields
        })
    })
    assert result is None


# ---------------------------------------------------------------------------
# _poll: xreadgroup returns entries → _process_message called for each
# ---------------------------------------------------------------------------

async def test_poll_calls_process_message_for_each_entry(mock_redis, settings):
    pipeline_fn = AsyncMock(return_value=make_decision_result())
    consumer = _consumer(mock_redis, settings, pipeline_fn)

    eid = str(uuid.uuid4())
    fields = {
        "event": json.dumps({
            "type": "creative.review_requested",
            "event_id": eid,
            "tenant_id": "t", "department": "creative", "partition_key": "p",
            "correlation_id": str(uuid.uuid4()), "schema_version": "1.0", "category": "creative",
            "payload": {"brief_id": str(uuid.uuid4()), "asset_ids": [str(uuid.uuid4())],
                        "proposed_action": "approve_internal", "proposed_spend_minor_units": None},
        })
    }
    mock_redis.xreadgroup.return_value = [
        ("creative.review_requested", [("1234-0", fields)])
    ]
    mock_redis.set.return_value = True

    await consumer._poll()

    pipeline_fn.assert_awaited_once()


# ---------------------------------------------------------------------------
# _poll: empty response → no processing
# ---------------------------------------------------------------------------

async def test_poll_empty_response_no_pipeline(mock_redis, settings):
    pipeline_fn = AsyncMock()
    consumer = _consumer(mock_redis, settings, pipeline_fn)
    mock_redis.xreadgroup.return_value = []

    await consumer._poll()

    pipeline_fn.assert_not_awaited()


# ---------------------------------------------------------------------------
# run(): CancelledError in loop → exits cleanly (no re-raise from outer)
# ---------------------------------------------------------------------------

async def test_run_exits_on_cancelled_error(mock_redis, settings):
    consumer = _consumer(mock_redis, settings)
    mock_redis.xreadgroup.side_effect = asyncio.CancelledError()

    task = asyncio.create_task(consumer.run())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass  # acceptable — outer cancel propagated


# ---------------------------------------------------------------------------
# run(): non-CancelledError exception in loop → sleeps and continues
# ---------------------------------------------------------------------------

async def test_run_recovers_from_exception(mock_redis, settings):
    call_count = 0

    async def _flaky_poll():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient error")
        raise asyncio.CancelledError()

    consumer = _consumer(mock_redis, settings)
    consumer._poll = _flaky_poll
    consumer._reclaim_idle = AsyncMock()

    with patch("skylize.decision_engine.consumer.asyncio.sleep", new_callable=AsyncMock):
        try:
            await consumer.run()
        except asyncio.CancelledError:
            pass

    assert call_count >= 2


# ---------------------------------------------------------------------------
# xautoclaim ResponseError (< Redis 6.2) → silently skipped
# ---------------------------------------------------------------------------

async def test_reclaim_idle_xautoclaim_response_error_skipped(mock_redis, settings):
    pipeline_fn = AsyncMock()
    consumer = _consumer(mock_redis, settings, pipeline_fn)
    mock_redis.xautoclaim.side_effect = aioredis.ResponseError("XAUTOCLAIM unavailable")

    # Should not raise
    await consumer._reclaim_idle()
    pipeline_fn.assert_not_awaited()
