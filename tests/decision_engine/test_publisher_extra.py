"""Extra publisher tests targeting uncovered helper functions."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock


from skylize.decision_engine.models import (
    DecisionOutcome,
    EvaluationStage,
    EvaluationStepRecord,
)
from skylize.decision_engine.publisher import (
    _audit_result_str,
    _extract_action_kind,
    _extract_approved_scope,
    _extract_authority_level,
    _extract_causation_id,
    _extract_correlation_id,
    _extract_department,
    _extract_governance_token_id,
    _extract_partition_key,
    _extract_proposing_agent,
    _find_rejecting_stage,
    _flatten_for_stream,
    DecisionEventPublisher,
)

from .conftest import make_decision_result


def _step(stage=EvaluationStage.AUTHORITY, passed=True, outcome=None, detail=None):
    return EvaluationStepRecord(
        stage=stage,
        passed=passed,
        outcome=outcome,
        detail=detail or {},
        duration_ms=1.0,
        timestamp=datetime.now(timezone.utc),
    )


def _result_with_step(detail: dict, outcome=DecisionOutcome.APPROVED):
    result = make_decision_result(outcome=outcome)
    result.steps = [_step(detail=detail)]
    return result


# ---------------------------------------------------------------------------
# _audit_result_str variants
# ---------------------------------------------------------------------------

def test_audit_result_str_rejected():
    step = _step(outcome=DecisionOutcome.REJECTED)
    assert _audit_result_str(step) == "denied"


def test_audit_result_str_deferred():
    step = _step(outcome=DecisionOutcome.DEFERRED_TO_HUMAN)
    assert _audit_result_str(step) == "escalated"


def test_audit_result_str_escalated():
    step = _step(outcome=DecisionOutcome.ESCALATED)
    assert _audit_result_str(step) == "escalated"


def test_audit_result_str_success():
    step = _step(outcome=None)
    assert _audit_result_str(step) == "success"


# ---------------------------------------------------------------------------
# _extract_correlation_id
# ---------------------------------------------------------------------------

def test_extract_correlation_id_from_step():
    cid = str(uuid.uuid4())
    result = _result_with_step({"correlation_id": cid})
    extracted = _extract_correlation_id(result)
    assert str(extracted) == cid


def test_extract_correlation_id_fallback_uuid4_when_missing():
    result = make_decision_result()
    result.steps = []
    extracted = _extract_correlation_id(result)
    assert isinstance(extracted, uuid.UUID)


def test_extract_correlation_id_invalid_value_skipped():
    result = _result_with_step({"correlation_id": "not-a-uuid"})
    # Falls through to uuid4 fallback
    extracted = _extract_correlation_id(result)
    assert isinstance(extracted, uuid.UUID)


# ---------------------------------------------------------------------------
# _extract_causation_id
# ---------------------------------------------------------------------------

def test_extract_causation_id_valid_event_id():
    eid = str(uuid.uuid4())
    result = make_decision_result(event_id=eid)
    assert str(_extract_causation_id(result)) == eid


def test_extract_causation_id_invalid_returns_none():
    result = make_decision_result()
    result.event_id = "not-a-uuid"
    assert _extract_causation_id(result) is None


# ---------------------------------------------------------------------------
# _extract_partition_key / _extract_proposing_agent / _extract_authority_level
# ---------------------------------------------------------------------------

def test_extract_partition_key_from_step():
    result = _result_with_step({"partition_key": "brief:42"})
    assert _extract_partition_key(result) == "brief:42"


def test_extract_partition_key_fallback_decision_id():
    result = make_decision_result()
    result.steps = []
    assert _extract_partition_key(result) == result.decision_id


def test_extract_proposing_agent_from_step():
    result = _result_with_step({"proposing_agent": "vp_agent"})
    assert _extract_proposing_agent(result) == "vp_agent"


def test_extract_proposing_agent_fallback():
    result = make_decision_result()
    result.steps = []
    assert _extract_proposing_agent(result) == "unknown"


def test_extract_authority_level_from_step():
    result = _result_with_step({"authority_level": "director"})
    assert _extract_authority_level(result) == "director"


def test_extract_authority_level_fallback():
    result = make_decision_result()
    result.steps = []
    assert _extract_authority_level(result) == "worker"


# ---------------------------------------------------------------------------
# _extract_action_kind / _extract_department
# ---------------------------------------------------------------------------

def test_extract_action_kind_action_kind_key():
    result = _result_with_step({"action_kind": "launch"})
    assert _extract_action_kind(result) == "launch"


def test_extract_action_kind_event_type_key_fallback():
    result = _result_with_step({"event_type": "creative.review_requested"})
    assert _extract_action_kind(result) == "creative.review_requested"


def test_extract_action_kind_unknown_fallback():
    result = make_decision_result()
    result.steps = []
    assert _extract_action_kind(result) == "unknown"


def test_extract_department_from_step():
    result = _result_with_step({"department": "sales"})
    assert _extract_department(result) == "sales"


def test_extract_department_fallback():
    result = make_decision_result()
    result.steps = []
    assert _extract_department(result) == "unknown"


# ---------------------------------------------------------------------------
# _extract_governance_token_id
# ---------------------------------------------------------------------------

def test_extract_governance_token_id_found():
    gt = str(uuid.uuid4())
    result = _result_with_step({"governance_token_id": gt})
    assert str(_extract_governance_token_id(result)) == gt


def test_extract_governance_token_id_not_found():
    result = make_decision_result()
    result.steps = []
    assert _extract_governance_token_id(result) is None


# ---------------------------------------------------------------------------
# _extract_approved_scope
# ---------------------------------------------------------------------------

def test_extract_approved_scope_from_step():
    result = _result_with_step({"approved_scope": {"k": "v"}})
    assert _extract_approved_scope(result) == {"k": "v"}


def test_extract_approved_scope_fallback_to_action_kind():
    result = _result_with_step({"action_kind": "stage"})
    scope = _extract_approved_scope(result)
    assert scope == {"action_kind": "stage"}


# ---------------------------------------------------------------------------
# _find_rejecting_stage
# ---------------------------------------------------------------------------

def test_find_rejecting_stage_found():
    result = make_decision_result(outcome=DecisionOutcome.REJECTED)
    result.steps = [_step(stage=EvaluationStage.OPA_POLICY, outcome=DecisionOutcome.REJECTED)]
    assert _find_rejecting_stage(result) == "OPA_POLICY"


def test_find_rejecting_stage_unknown():
    result = make_decision_result()
    result.steps = []
    assert _find_rejecting_stage(result) == "unknown"


# ---------------------------------------------------------------------------
# _flatten_for_stream
# ---------------------------------------------------------------------------

def test_flatten_for_stream_nested():
    data = {"a": {"b": 1}, "c": [1, 2]}
    flat = _flatten_for_stream(data)
    assert flat["a.b"] == "1"
    assert flat["c"] == "[1, 2]"


def test_flatten_for_stream_none_values():
    flat = _flatten_for_stream({"x": None})
    assert flat["x"] == ""


# ---------------------------------------------------------------------------
# publish_outcome with step-carried metadata (source_agent_id path)
# ---------------------------------------------------------------------------

def _publisher_with_conn(settings, conn=None, redis=None):
    c = conn or AsyncMock()
    r = redis or AsyncMock()
    db = MagicMock()

    @asynccontextmanager
    async def _tenant_session(tenant_id: str):
        yield c

    db.tenant_session = _tenant_session
    return DecisionEventPublisher(redis=r, db=db, settings=settings), c, r


async def test_publish_with_step_metadata(settings):
    pub, conn, redis = _publisher_with_conn(settings)
    result = make_decision_result(outcome=DecisionOutcome.APPROVED)
    result.steps = [_step(detail={
        "department": "creative",
        "action_kind": "launch",
        "authority_level": "director",
        "approved_scope": {"action": "launch"},
    })]

    await pub.publish_outcome(result)

    redis.xadd.assert_awaited_once()


async def test_dead_letter_xadd_fail_logged(settings, caplog):
    redis = AsyncMock()
    redis.xadd.side_effect = Exception("all xadd fail")
    pub, conn, _ = _publisher_with_conn(settings, redis=redis)
    result = make_decision_result(outcome=DecisionOutcome.APPROVED)

    # Should not raise — dead_letter failure is swallowed
    await pub.publish_outcome(result)
