"""Tests for EvaluationPipeline — all 6 stages."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skylize.decision_engine.capital_dal import CapitalDAL
from skylize.decision_engine.exceptions import EvaluationTimeout, OPAPolicyDenied
from skylize.decision_engine.models import (
    CapitalCheckResult,
    DecisionContext,
    DecisionOutcome,
    RiskBand,
    ScoringResult,
)
from skylize.decision_engine.opa_client import OPAClient
from skylize.decision_engine.pipeline import EvaluationPipeline
from skylize.decision_engine.scoring import ScoringEngine

from .conftest import make_decision_context, make_scoring_result


def _make_pipeline(
    settings,
    opa_allow: bool = True,
    opa_deny_reasons: list[str] | None = None,
    scoring_override: ScoringResult | None = None,
    capital_passes: bool = True,
    capital_requested: Decimal | None = None,
    event_bus: Any | None = None,
) -> EvaluationPipeline:
    opa = MagicMock(spec=OPAClient)
    opa.evaluate = AsyncMock(return_value=(opa_allow, opa_deny_reasons or []))

    scoring_eng = MagicMock(spec=ScoringEngine)
    if scoring_override:
        scoring_eng.score.return_value = scoring_override
    else:
        scoring_eng.score.return_value = make_scoring_result(
            risk_score=25.0, opp_score=55.0, risk_band=RiskBand.LOW
        )
    ScoringEngine.lookup_matrix = staticmethod(ScoringEngine.lookup_matrix)

    capital = MagicMock(spec=CapitalDAL)
    capital.extract_requested_amount = AsyncMock(return_value=capital_requested)
    if capital_requested is not None:
        capital_result = CapitalCheckResult(
            available_budget=Decimal("10000"),
            requested_amount=capital_requested,
            ceiling_pct=float(capital_requested) / 100.0,
            passes=capital_passes,
            reason="ok" if capital_passes else "SPEND_OVER_CEILING: ...",
        )
        capital.check_capital_ceiling = AsyncMock(return_value=capital_result)

    return EvaluationPipeline(
        opa_client=opa,
        scoring_engine=scoring_eng,
        capital_dal=capital,
        settings=settings,
        event_bus=event_bus,
    )


def _valid_ctx(**kw) -> DecisionContext:
    return make_decision_context(
        department="creative",
        event_type="creative.review_requested",
        **kw,
    )


# ---------------------------------------------------------------------------
# Full GO path: all 6 stages pass → APPROVED
# ---------------------------------------------------------------------------

async def test_full_go_path_approved(settings):
    pipeline = _make_pipeline(settings, opa_allow=True)
    ctx = _valid_ctx()

    result = await pipeline.evaluate(ctx)

    assert result.outcome == DecisionOutcome.APPROVED
    assert len(result.steps) == 6


# ---------------------------------------------------------------------------
# Stage 1 AUTHORITY fail → REJECTED, pipeline stops, only 1 step recorded
# ---------------------------------------------------------------------------

async def test_authority_fail_rejected_one_step(settings):
    pipeline = _make_pipeline(settings)
    ctx = make_decision_context(department="unknown_dept", event_type="unknown.event")

    result = await pipeline.evaluate(ctx)

    assert result.outcome == DecisionOutcome.REJECTED
    assert len(result.steps) == 1
    assert result.steps[0].stage.value == "AUTHORITY"


# ---------------------------------------------------------------------------
# OPA deny → REJECTED, stages 1–2 recorded
# ---------------------------------------------------------------------------

async def test_opa_deny_rejected_two_steps(settings):
    pipeline = _make_pipeline(settings, opa_allow=False, opa_deny_reasons=["policy_violated"])
    ctx = _valid_ctx()

    result = await pipeline.evaluate(ctx)

    assert result.outcome == DecisionOutcome.REJECTED
    assert len(result.steps) == 2
    assert result.steps[1].stage.value == "OPA_POLICY"


# ---------------------------------------------------------------------------
# CRITICAL risk band → REJECTED at stage 3
# ---------------------------------------------------------------------------

async def test_critical_risk_band_rejected_at_stage3(settings):
    crit_scoring = make_scoring_result(risk_score=90.0, opp_score=80.0, risk_band=RiskBand.CRITICAL)
    pipeline = _make_pipeline(settings, scoring_override=crit_scoring)
    ctx = _valid_ctx()

    result = await pipeline.evaluate(ctx)

    assert result.outcome == DecisionOutcome.REJECTED
    assert len(result.steps) == 3
    assert result.steps[2].stage.value == "SCORING"


# ---------------------------------------------------------------------------
# Capital ceiling exceeded → REJECTED at stage 4
# ---------------------------------------------------------------------------

async def test_capital_ceiling_exceeded_rejected_stage4(settings):
    pipeline = _make_pipeline(
        settings,
        capital_requested=Decimal("99999"),
        capital_passes=False,
    )
    ctx = _valid_ctx()

    result = await pipeline.evaluate(ctx)

    assert result.outcome == DecisionOutcome.REJECTED
    assert len(result.steps) == 4
    assert result.steps[3].stage.value == "CAPITAL"


# ---------------------------------------------------------------------------
# Conflict detected → DEFERRED_TO_HUMAN at stage 5
# ---------------------------------------------------------------------------

async def test_conflict_detected_deferred_stage5(settings):
    pipeline = _make_pipeline(settings)
    # payload has both approval and rejection signals → conflict
    ctx = _valid_ctx(payload={"approve": True, "reject": True})

    result = await pipeline.evaluate(ctx)

    assert result.outcome == DecisionOutcome.DEFERRED_TO_HUMAN
    assert len(result.steps) == 5
    assert result.steps[4].stage.value == "CONFLICT"


# ---------------------------------------------------------------------------
# ESCALATED matrix outcome → DEFERRED_TO_HUMAN at stage 6
# ---------------------------------------------------------------------------

async def test_escalated_matrix_outcome_deferred_stage6(settings):
    # MED risk + LOW opp → DEFERRED_TO_HUMAN from matrix
    med_low_scoring = make_scoring_result(risk_score=45.0, opp_score=20.0, risk_band=RiskBand.MED)
    pipeline = _make_pipeline(settings, scoring_override=med_low_scoring)
    ctx = _valid_ctx()

    result = await pipeline.evaluate(ctx)

    assert result.outcome == DecisionOutcome.DEFERRED_TO_HUMAN
    assert len(result.steps) == 6


# ---------------------------------------------------------------------------
# Audit event emitted for EVERY stage (assert event_bus.publish called N times)
# ---------------------------------------------------------------------------

async def test_audit_event_emitted_per_stage(settings):
    publish_mock = AsyncMock()
    bus = MagicMock()
    bus.publish = publish_mock

    pipeline = _make_pipeline(settings, event_bus=bus)
    ctx = _valid_ctx()

    result = await pipeline.evaluate(ctx)

    # Wait briefly for fire-and-forget tasks
    await asyncio.sleep(0.05)

    n_steps = len(result.steps)
    # each step → one asyncio.ensure_future(_publish()) call
    assert publish_mock.await_count == n_steps


# ---------------------------------------------------------------------------
# Timeout → EvaluationTimeout raised
# ---------------------------------------------------------------------------

async def test_timeout_raises_evaluation_timeout(settings):
    pipeline = _make_pipeline(settings)

    async def _slow_stages(ctx):
        await asyncio.sleep(100)

    with patch.object(pipeline, "_run_stages", side_effect=asyncio.TimeoutError):
        with pytest.raises(EvaluationTimeout):
            await pipeline.evaluate(_valid_ctx())


# ---------------------------------------------------------------------------
# Audit publish failure does NOT fail pipeline
# ---------------------------------------------------------------------------

async def test_audit_publish_failure_does_not_fail_pipeline(settings):
    publish_mock = AsyncMock(side_effect=RuntimeError("audit bus down"))
    bus = MagicMock()
    bus.publish = publish_mock

    pipeline = _make_pipeline(settings, event_bus=bus)
    ctx = _valid_ctx()

    # Should not raise
    result = await pipeline.evaluate(ctx)
    assert result.outcome is not None


# ---------------------------------------------------------------------------
# OPA exception (not just deny) → REJECTED
# ---------------------------------------------------------------------------

async def test_opa_exception_causes_rejected(settings):
    opa = MagicMock(spec=OPAClient)
    opa.evaluate = AsyncMock(side_effect=OPAPolicyDenied("path", "timeout"))

    scoring_eng = MagicMock(spec=ScoringEngine)
    capital = MagicMock(spec=CapitalDAL)

    pipeline = EvaluationPipeline(
        opa_client=opa,
        scoring_engine=scoring_eng,
        capital_dal=capital,
        settings=settings,
    )
    ctx = _valid_ctx()
    result = await pipeline.evaluate(ctx)

    assert result.outcome == DecisionOutcome.REJECTED
