"""Tests for DecisionEventPublisher (transactional outbox producer).

The publisher no longer touches Redis: it writes the ``decisions`` row and a
``decision_outbox`` row in ONE ``tenant_session`` transaction (a single CTE), and
the OutboxPoller relays the outbox row. These tests assert that one transactional
write and the values that land in the outbox row.
"""
from __future__ import annotations

import re
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


def _publisher(settings, conn=None):
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

    pub = DecisionEventPublisher(db=db, settings=settings)
    return pub, c


# Positional-arg indices into conn.execute(sql, *args) for the outbox CTE
# (args[0] is the SQL; $1..$20 follow, so $N == args[N]).
_ARG_OUTCOME_DB = 10
_ARG_STREAM_KEY = 17
_ARG_EVENT_TYPE = 18
_ARG_OUTBOX_ROW_ID = 20


async def _publish(settings, outcome, tenant_id="acme"):
    pub, conn = _publisher(settings)
    result = make_decision_result(outcome=outcome, tenant_id=tenant_id)
    await pub.publish_outcome(result)
    conn.execute.assert_awaited_once()
    return conn.execute.call_args.args


# ---------------------------------------------------------------------------
# One transactional write: decisions + decision_outbox in a single statement
# ---------------------------------------------------------------------------

async def test_approved_writes_decision_and_outbox(settings):
    args = await _publish(settings, DecisionOutcome.APPROVED, tenant_id="acme")
    sql = args[0]
    assert "INSERT INTO decisions" in sql
    assert "INSERT INTO decision_outbox" in sql
    assert args[_ARG_OUTCOME_DB] == "approved"
    assert args[_ARG_EVENT_TYPE] == "decision.approved"
    assert args[_ARG_STREAM_KEY] == "evt:acme:decision"


async def test_rejected_writes_rejected_event(settings):
    args = await _publish(settings, DecisionOutcome.REJECTED)
    assert args[_ARG_EVENT_TYPE] == "decision.rejected"
    assert args[_ARG_STREAM_KEY].endswith(":decision")


async def test_deferred_writes_deferred_event(settings):
    args = await _publish(settings, DecisionOutcome.DEFERRED_TO_HUMAN)
    assert args[_ARG_EVENT_TYPE] == "decision.deferred_to_human"
    assert args[_ARG_STREAM_KEY].endswith(":decision")


async def test_escalated_routes_to_governance_stream(settings):
    args = await _publish(settings, DecisionOutcome.ESCALATED, tenant_id="acme")
    assert args[_ARG_EVENT_TYPE] == "governance.human_escalation_raised"
    assert args[_ARG_STREAM_KEY] == "evt:acme:governance"
    # ESCALATED persists as deferred_to_human in the decisions CHECK vocabulary.
    assert args[_ARG_OUTCOME_DB] == "deferred_to_human"


# ---------------------------------------------------------------------------
# outbox_row_id is a Redis-valid stream id ({unix_ms}-{seq})
# ---------------------------------------------------------------------------

async def test_outbox_row_id_is_redis_stream_id(settings):
    args = await _publish(settings, DecisionOutcome.APPROVED)
    assert re.fullmatch(r"\d+-\d{4}", args[_ARG_OUTBOX_ROW_ID])


# ---------------------------------------------------------------------------
# Invalid payload fails validation before any I/O
# ---------------------------------------------------------------------------

async def test_invalid_payload_raises_before_io(settings):
    pub, conn = _publisher(settings)

    async def _bad_payload(result):
        return {"bad": "data"}  # missing required decision_id etc.

    pub._build_outbound_payload = _bad_payload
    result = make_decision_result(outcome=DecisionOutcome.APPROVED)

    with pytest.raises(DecisionEngineError):
        await pub.publish_outcome(result)

    conn.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# mirror_audit_step inserts to audit_log
# ---------------------------------------------------------------------------

async def test_mirror_audit_step_inserts_row(settings):
    pub, conn = _publisher(settings)

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


async def test_mirror_audit_step_wraps_postgres_error(settings):
    conn = AsyncMock()
    conn.execute.side_effect = asyncpg.PostgresError("pg fail")

    pub, _ = _publisher(settings, conn=conn)
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
