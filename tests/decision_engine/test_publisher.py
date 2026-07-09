"""Tests for DecisionEventPublisher."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from skylize.decision_engine.exceptions import DecisionEngineError
from skylize.decision_engine.models import (
    DecisionOutcome,
    EvaluationStage,
    EvaluationStepRecord,
)
from skylize.decision_engine.publisher import DecisionEventPublisher

from .conftest import make_decision_result


def _publisher(settings, redis=None, conn=None) -> tuple[DecisionEventPublisher, AsyncMock, AsyncMock]:
    r = redis or AsyncMock()
    c = conn or AsyncMock()

    db = MagicMock()

    @asynccontextmanager
    async def _tenant_session(tenant_id: str):
        yield c

    @asynccontextmanager
    async def _admin_session():
        yield c

    db.tenant_session = _tenant_session
    db.admin_session = _admin_session

    pub = DecisionEventPublisher(redis=r, db=db, settings=settings)
    return pub, r, c


# ---------------------------------------------------------------------------
# APPROVED → decision.approved event published to correct stream
# ---------------------------------------------------------------------------

async def test_approved_publishes_to_decisions_stream(settings):
    pub, redis, _ = _publisher(settings)
    result = make_decision_result(outcome=DecisionOutcome.APPROVED, tenant_id="acme")

    await pub.publish_outcome(result)

    redis.xadd.assert_awaited_once()
    stream_key = redis.xadd.call_args.args[0]
    assert stream_key == "evt:acme:decisions"

    fields = redis.xadd.call_args.args[1]
    assert fields["event_type"] == "decision.approved"


# ---------------------------------------------------------------------------
# DEFERRED_TO_HUMAN → governance.human_escalation_raised ... wait, spec says
# DEFERRED → decision.deferred_to_human published.
# ESCALATED → governance.human_escalation_raised published
# ---------------------------------------------------------------------------

async def test_deferred_publishes_deferred_event(settings):
    pub, redis, _ = _publisher(settings)
    result = make_decision_result(outcome=DecisionOutcome.DEFERRED_TO_HUMAN)

    await pub.publish_outcome(result)

    fields = redis.xadd.call_args.args[1]
    assert fields["event_type"] == "decision.deferred_to_human"


async def test_escalated_publishes_governance_event(settings):
    pub, redis, _ = _publisher(settings)
    result = make_decision_result(outcome=DecisionOutcome.ESCALATED)

    await pub.publish_outcome(result)

    fields = redis.xadd.call_args.args[1]
    assert fields["event_type"] == "governance.human_escalation_raised"


# ---------------------------------------------------------------------------
# Postgres write before Redis publish (assert call order)
# ---------------------------------------------------------------------------

async def test_postgres_written_before_redis_publish(settings):
    call_log: list[str] = []

    conn = AsyncMock()

    async def _execute(*args, **kwargs):
        call_log.append("postgres")

    conn.execute = _execute

    redis = AsyncMock()

    async def _xadd(*args, **kwargs):
        call_log.append("redis")
        return "1234-0"

    redis.xadd = _xadd

    pub, _, _ = _publisher(settings, redis=redis, conn=conn)
    result = make_decision_result(outcome=DecisionOutcome.APPROVED)

    await pub.publish_outcome(result)

    postgres_idx = call_log.index("postgres")
    redis_idx = call_log.index("redis")
    assert postgres_idx < redis_idx


# ---------------------------------------------------------------------------
# Redis failure after Postgres: Postgres not rolled back, error logged
# ---------------------------------------------------------------------------

async def test_redis_failure_does_not_rollback_postgres(settings, caplog):
    conn = AsyncMock()
    redis = AsyncMock()
    redis.xadd.side_effect = [
        Exception("redis down"),  # main publish fails
        "1234-0",                  # DLQ xadd succeeds
    ]

    pub, _, _ = _publisher(settings, redis=redis, conn=conn)
    result = make_decision_result(outcome=DecisionOutcome.APPROVED)

    await pub.publish_outcome(result)

    # Postgres execute should have been called
    conn.execute.assert_awaited()

    # Second xadd call = dead letter
    assert redis.xadd.await_count == 2


# ---------------------------------------------------------------------------
# Invalid payload fails Pydantic validation before any I/O
# ---------------------------------------------------------------------------

async def test_invalid_payload_raises_before_io(settings):
    pub, redis, conn = _publisher(settings)

    # Patch _build_outbound_payload to return garbage
    async def _bad_payload(result):
        return {"bad": "data"}  # missing required decision_id etc.

    pub._build_outbound_payload = _bad_payload

    result = make_decision_result(outcome=DecisionOutcome.APPROVED)

    with pytest.raises(DecisionEngineError):
        await pub.publish_outcome(result)

    conn.execute.assert_not_awaited()
    redis.xadd.assert_not_awaited()


# ---------------------------------------------------------------------------
# REJECTED → decision.rejected event
# ---------------------------------------------------------------------------

async def test_rejected_publishes_rejected_event(settings):
    pub, redis, _ = _publisher(settings)
    result = make_decision_result(outcome=DecisionOutcome.REJECTED)

    await pub.publish_outcome(result)

    fields = redis.xadd.call_args.args[1]
    assert fields["event_type"] == "decision.rejected"


# ---------------------------------------------------------------------------
# mirror_audit_step inserts to audit_log
# ---------------------------------------------------------------------------

async def test_mirror_audit_step_inserts_row(settings):
    pub, _, conn = _publisher(settings)

    step = EvaluationStepRecord(
        stage=EvaluationStage.AUTHORITY,
        passed=True,
        outcome=None,
        detail={},
        duration_ms=1.0,
        timestamp=datetime.now(timezone.utc),
    )

    await pub.mirror_audit_step("tenant-a", step, str(uuid.uuid4()))

    conn.execute.assert_awaited_once()
    sql = conn.execute.call_args.args[0]
    assert "INSERT INTO audit_log" in sql


# ---------------------------------------------------------------------------
# mirror_audit_step wraps asyncpg error in DecisionEngineError
# ---------------------------------------------------------------------------

async def test_mirror_audit_step_wraps_postgres_error(settings):
    conn = AsyncMock()
    conn.execute.side_effect = asyncpg.PostgresError("pg fail")

    pub, _, _ = _publisher(settings, conn=conn)
    step = EvaluationStepRecord(
        stage=EvaluationStage.SCORING,
        passed=False,
        outcome=DecisionOutcome.REJECTED,
        detail={},
        duration_ms=2.0,
        timestamp=datetime.now(timezone.utc),
    )

    with pytest.raises(DecisionEngineError):
        await pub.mirror_audit_step("tenant-a", step, str(uuid.uuid4()))
