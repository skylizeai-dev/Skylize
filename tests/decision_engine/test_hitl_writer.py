"""Tests for HITLQueueWriter."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from skylize.decision_engine.hitl_writer import HITLQueueWriter
from skylize.decision_engine.models import DecisionOutcome

from .conftest import make_decision_context, make_decision_result


def _writer(settings, conn=None, redis=None) -> tuple[HITLQueueWriter, AsyncMock, AsyncMock]:
    c = conn or AsyncMock()
    r = redis or AsyncMock()

    db = MagicMock()

    @asynccontextmanager
    async def _tenant_session(tenant_id: str):
        yield c

    db.tenant_session = _tenant_session

    writer = HITLQueueWriter(db=db, redis=r, settings=settings)
    return writer, c, r


# ---------------------------------------------------------------------------
# Escalation record inserted with all required fields
# ---------------------------------------------------------------------------

async def test_write_escalation_inserts_all_required_fields(settings):
    writer, conn, redis = _writer(settings)
    ctx = make_decision_context()
    result = make_decision_result(outcome=DecisionOutcome.DEFERRED_TO_HUMAN)

    hitl_id = await writer.write_escalation(ctx, result, uuid.uuid4())

    conn.execute.assert_awaited_once()
    sql, *args = conn.execute.call_args.args
    assert "INSERT INTO hitl_queue" in sql

    # Required columns present in INSERT
    for col in ("hitl_id", "org_id", "decision_id", "trigger_reason", "status", "expires_at"):
        assert col in sql

    assert hitl_id  # non-empty string


# ---------------------------------------------------------------------------
# Governance event emitted after successful insert
# ---------------------------------------------------------------------------

async def test_governance_event_emitted_after_insert(settings):
    writer, conn, redis = _writer(settings)
    ctx = make_decision_context()
    result = make_decision_result(outcome=DecisionOutcome.DEFERRED_TO_HUMAN)

    await writer.write_escalation(ctx, result, uuid.uuid4())

    redis.xadd.assert_awaited_once()
    stream_key = redis.xadd.call_args.args[0]
    assert "governance" in stream_key
    fields = redis.xadd.call_args.args[1]
    assert fields["event_type"] == "governance.human_escalation_raised"


# ---------------------------------------------------------------------------
# Duplicate event_id → write skipped (check_duplicate_escalation returns True)
# ---------------------------------------------------------------------------

async def test_duplicate_escalation_skipped(settings):
    conn = AsyncMock()
    # fetchrow returns a row → duplicate exists
    conn.fetchrow.return_value = {"hitl_id": str(uuid.uuid4())}
    writer, _, redis = _writer(settings, conn=conn)

    is_dup = await writer.check_duplicate_escalation("event-123", "tenant-abc")
    assert is_dup is True


async def test_no_duplicate_when_no_row(settings):
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    writer, _, _ = _writer(settings, conn=conn)

    is_dup = await writer.check_duplicate_escalation("event-new", "tenant-abc")
    assert is_dup is False


# ---------------------------------------------------------------------------
# Postgres insert failure → Redis event NOT emitted
# ---------------------------------------------------------------------------

async def test_postgres_failure_does_not_emit_redis(settings):
    conn = AsyncMock()
    conn.execute.side_effect = asyncpg.PostgresError("insert failed")
    writer, _, redis = _writer(settings, conn=conn)

    ctx = make_decision_context()
    result = make_decision_result(outcome=DecisionOutcome.DEFERRED_TO_HUMAN)

    with pytest.raises(asyncpg.PostgresError):
        await writer.write_escalation(ctx, result, uuid.uuid4())

    redis.xadd.assert_not_awaited()


# ---------------------------------------------------------------------------
# expires_at set correctly (48h from now, within 1s tolerance)
# ---------------------------------------------------------------------------

async def test_expires_at_48h_from_now(settings):
    now_before = datetime.now(timezone.utc)

    insert_args_capture: list = []

    async def _capture_execute(sql, *args):
        insert_args_capture.extend(args)

    conn = AsyncMock()
    conn.execute = _capture_execute

    writer, _, _ = _writer(settings, conn=conn)
    ctx = make_decision_context()
    result = make_decision_result(outcome=DecisionOutcome.DEFERRED_TO_HUMAN)

    await writer.write_escalation(ctx, result, uuid.uuid4())

    now_after = datetime.now(timezone.utc)

    # expires_at is arg index 9 (0-based) in the INSERT
    # hitl_id=0, org_id=1, decision_id=2, correlation_id=3, partition_key=4,
    # trigger_reason=5, proposal_json=6, score_json=7, status=8, expires_at=9, created_at=10
    expires_at = insert_args_capture[9]

    expected_low = now_before + timedelta(hours=48)
    expected_high = now_after + timedelta(hours=48)

    assert isinstance(expires_at, datetime)
    assert expected_low <= expires_at <= expected_high + timedelta(seconds=1)


# ---------------------------------------------------------------------------
# ESCALATED outcome also accepted
# ---------------------------------------------------------------------------

async def test_escalated_outcome_accepted(settings):
    writer, conn, redis = _writer(settings)
    ctx = make_decision_context()
    result = make_decision_result(outcome=DecisionOutcome.ESCALATED)

    hitl_id = await writer.write_escalation(ctx, result, uuid.uuid4())

    conn.execute.assert_awaited_once()
    assert hitl_id


# ---------------------------------------------------------------------------
# Non-eligible outcome raises ValueError, no DB or Redis calls
# ---------------------------------------------------------------------------

async def test_non_eligible_outcome_raises_value_error(settings):
    writer, conn, redis = _writer(settings)
    ctx = make_decision_context()
    result = make_decision_result(outcome=DecisionOutcome.APPROVED)

    with pytest.raises(ValueError, match="non-escalation outcome"):
        await writer.write_escalation(ctx, result, uuid.uuid4())

    conn.execute.assert_not_awaited()
    redis.xadd.assert_not_awaited()


# ---------------------------------------------------------------------------
# Redis failure after insert does NOT raise (warning only)
# ---------------------------------------------------------------------------

async def test_redis_failure_after_insert_does_not_raise(settings):
    redis = AsyncMock()
    redis.xadd.side_effect = Exception("redis down")

    writer, _, _ = _writer(settings, redis=redis)
    ctx = make_decision_context()
    result = make_decision_result(outcome=DecisionOutcome.DEFERRED_TO_HUMAN)

    # Should not raise — Redis failure is warning-only
    hitl_id = await writer.write_escalation(ctx, result, uuid.uuid4())
    assert hitl_id


# ---------------------------------------------------------------------------
# write_escalation uses the caller-supplied hitl_id verbatim (single-mint
# contract) — it does not mint its own, and returns + emits the same value.
# ---------------------------------------------------------------------------

async def test_write_escalation_uses_supplied_hitl_id(settings):
    writer, conn, redis = _writer(settings)
    ctx = make_decision_context()
    result = make_decision_result(outcome=DecisionOutcome.DEFERRED_TO_HUMAN)
    supplied_hitl_id = uuid.uuid4()

    returned_hitl_id = await writer.write_escalation(ctx, result, supplied_hitl_id)

    assert returned_hitl_id == str(supplied_hitl_id)

    sql, *args = conn.execute.call_args.args
    inserted_hitl_id = args[0]
    assert inserted_hitl_id == supplied_hitl_id

    fields = redis.xadd.call_args.args[1]
    assert fields["hitl_queue_id"] == str(supplied_hitl_id)
