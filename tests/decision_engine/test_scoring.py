"""Tests for ScoringEngine — deterministic, no I/O."""
from __future__ import annotations

import math

import pytest

from skylize.decision_engine.models import DecisionOutcome, RiskBand
from skylize.decision_engine.scoring import ScoringEngine
from skylize.decision_engine.constants import opportunity_bucket

from .conftest import make_decision_context


@pytest.fixture()
def engine(settings) -> ScoringEngine:
    return ScoringEngine(settings)


# ---------------------------------------------------------------------------
# Risk score — known payload → exact expected value
# ---------------------------------------------------------------------------

def test_risk_score_known_payload(engine):
    ctx = make_decision_context(payload={
        "requested_amount": 500.0,
        "budget_ceiling": 1000.0,
        "historical_failure_rate": 0.4,
        "confidence": 0.8,
        # no mitigation_plan, reversible not set → event_type not irreversible → rev=0
    })
    score, factors = engine.compute_risk_score(ctx)

    # downside = 500/1000 = 0.5, prob=0.4, rev=0, conf_penalty=0.2, hedge=0
    # raw = 0.5*0.4 + 0 + 0.2 - 0 = 0.4
    raw_expected = 0.5 * 0.4 + 0.0 + 0.2 - 0.0
    score_expected = max(0.0, min(1.0, (raw_expected - (-0.5)) / (3.0 - (-0.5)))) * 100.0
    assert abs(score - score_expected) < 0.001
    assert factors["risk.downside_magnitude"] == pytest.approx(0.5, abs=1e-4)
    assert factors["risk.probability_weight"] == pytest.approx(0.4, abs=1e-4)


def test_risk_score_severity_override(engine):
    ctx = make_decision_context(payload={"severity": 0.9, "confidence": 1.0})
    score, factors = engine.compute_risk_score(ctx)
    assert factors["risk.downside_magnitude"] == pytest.approx(0.9, abs=1e-4)


def test_risk_score_mitigation_plan_lowers_risk(engine):
    ctx_no_hedge = make_decision_context(payload={"severity": 0.5, "confidence": 0.5})
    ctx_hedged = make_decision_context(payload={
        "severity": 0.5, "confidence": 0.5, "mitigation_plan": "covered"
    })
    score_no, _ = engine.compute_risk_score(ctx_no_hedge)
    score_hedge, _ = engine.compute_risk_score(ctx_hedged)
    assert score_hedge < score_no


def test_risk_score_reversible_true_eliminates_penalty(engine):
    ctx = make_decision_context(
        event_type="sales.budget_committed",  # irreversible by event type
        payload={"reversible": True},
    )
    _, factors = engine.compute_risk_score(ctx)
    assert factors["risk.reversibility_penalty"] == 0.0


def test_risk_score_irreversible_event_type_penalty(engine):
    ctx = make_decision_context(
        event_type="sales.budget_committed",  # in _IRREVERSIBLE_EVENT_TYPES
        payload={},
    )
    _, factors = engine.compute_risk_score(ctx)
    assert factors["risk.reversibility_penalty"] == 1.0


def test_risk_score_defaults_no_exceptions(engine):
    ctx = make_decision_context(payload={})
    score, _ = engine.compute_risk_score(ctx)
    assert 0.0 <= score <= 100.0


# ---------------------------------------------------------------------------
# Opportunity score — known payload → exact expected value
# ---------------------------------------------------------------------------

def test_opportunity_score_known_payload(engine):
    ctx = make_decision_context(payload={
        "expected_roi": 4.0,
        "strategic_alignment": "high",
        "urgency": 0.5,
        "resource_intensity": 0.2,
        "execution_risk": 0.2,
    })
    score, factors = engine.compute_opportunity_score(ctx)

    roi_norm = min(1.0, math.log1p(4.0) / math.log1p(9.0))
    alignment = 0.80  # "high"
    tdf = 1.0 - (1.0 - 0.5) ** 2  # 0.75
    base = roi_norm * alignment
    urgency_bonus = base * tdf * 0.20
    raw = (base + urgency_bonus) - 0.2 * 0.35 - 0.2 * 0.45
    expected = max(0.0, min(1.0, raw)) * 100.0

    assert abs(score - expected) < 0.01
    assert factors["opp.strategic_alignment"] == pytest.approx(alignment, abs=1e-4)


def test_opportunity_score_zero_roi_no_alignment(engine):
    ctx = make_decision_context(payload={
        "expected_roi": 0.0,
        "strategic_alignment": "none",
        "resource_intensity": 0.0,
        "execution_risk": 0.0,
    })
    score, _ = engine.compute_opportunity_score(ctx)
    assert score == pytest.approx(0.0, abs=0.01)


def test_opportunity_score_defaults_no_exceptions(engine):
    ctx = make_decision_context(payload={})
    score, _ = engine.compute_opportunity_score(ctx)
    assert 0.0 <= score <= 100.0


# ---------------------------------------------------------------------------
# RiskBand boundaries (edge cases at 30, 60, 85, 100)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("score,expected_band", [
    (0.0, RiskBand.LOW),
    (30.0, RiskBand.LOW),      # inclusive upper of LOW
    (30.1, RiskBand.MED),
    (60.0, RiskBand.MED),      # inclusive upper of MED
    (60.1, RiskBand.HIGH),
    (85.0, RiskBand.HIGH),     # inclusive upper of HIGH
    (85.1, RiskBand.CRITICAL),
    (100.0, RiskBand.CRITICAL),
])
def test_risk_band_boundaries(score, expected_band):
    assert ScoringEngine.compute_risk_band(score) == expected_band


# ---------------------------------------------------------------------------
# DECISION_MATRIX — all 9 cells
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("risk_band,opp_score,expected_outcome", [
    (RiskBand.LOW,  20.0, DecisionOutcome.APPROVED),
    (RiskBand.LOW,  55.0, DecisionOutcome.APPROVED),
    (RiskBand.LOW,  80.0, DecisionOutcome.APPROVED),
    (RiskBand.MED,  20.0, DecisionOutcome.DEFERRED_TO_HUMAN),
    (RiskBand.MED,  55.0, DecisionOutcome.APPROVED),
    (RiskBand.MED,  80.0, DecisionOutcome.APPROVED),
    (RiskBand.HIGH, 20.0, DecisionOutcome.REJECTED),
    (RiskBand.HIGH, 55.0, DecisionOutcome.DEFERRED_TO_HUMAN),
    (RiskBand.HIGH, 80.0, DecisionOutcome.DEFERRED_TO_HUMAN),
])
def test_decision_matrix_all_cells(risk_band, opp_score, expected_outcome):
    result = ScoringEngine.lookup_matrix(risk_band, opp_score)
    assert result == expected_outcome


def test_critical_risk_always_deferred(engine):
    outcome = ScoringEngine.lookup_matrix(RiskBand.CRITICAL, 100.0)
    assert outcome == DecisionOutcome.DEFERRED_TO_HUMAN


# ---------------------------------------------------------------------------
# opportunity_bucket boundaries
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("score,expected_bucket", [
    (0.0, "LOW"),
    (40.0, "LOW"),
    (40.1, "MED"),
    (70.0, "MED"),
    (70.1, "HIGH"),
    (100.0, "HIGH"),
])
def test_opportunity_bucket_boundaries(score, expected_bucket):
    assert opportunity_bucket(score) == expected_bucket


# ---------------------------------------------------------------------------
# full score() method returns ScoringResult with correct structure
# ---------------------------------------------------------------------------

def test_score_method_returns_scoring_result(engine):
    ctx = make_decision_context(payload={
        "expected_roi": 2.0,
        "strategic_alignment": "medium",
        "confidence": 0.8,
    })
    result = engine.score(ctx)

    assert 0.0 <= result.risk_score <= 100.0
    assert 0.0 <= result.opportunity_score <= 100.0
    assert result.risk_band in RiskBand
    assert 0.0 <= result.confidence <= 1.0
    assert "risk.downside_magnitude" in result.factors
    assert "opp.expected_roi_norm" in result.factors


# ---------------------------------------------------------------------------
# Missing payload fields use documented defaults, no exceptions
# ---------------------------------------------------------------------------

def test_missing_all_fields_uses_defaults_no_exception(engine):
    ctx = make_decision_context(payload={})
    result = engine.score(ctx)
    # defaults: failure_rate=0.3, confidence=0.7, urgency=0, alignment=medium, etc.
    assert result.risk_score is not None
    assert result.opportunity_score is not None
