"""
Deterministic scoring engine for the Decision Engine (STAGE 3).

No I/O, no LLM calls. Pure computation: same inputs → same outputs, always.
Every score decomposes into named, weighted contributions for full explainability
(scoring_models.md §3, §5).

Expected payload keys (all optional; safe defaults apply when absent):
  Risk inputs:
    requested_amount     float  – spend requested; compared to budget_ceiling (default 0)
    budget_ceiling       float  – org/scope ceiling; default 1.0 (full budget = 0 downside)
    severity             float  – 0–1 explicit severity override (overrides amount calc)
    historical_failure_rate float – 0–1; default 0.3
    reversible           bool   – explicit flag; overrides event_type map when present
    confidence           float  – data/signal confidence 0–1; default 0.7
    mitigation_plan      any    – non-null presence lowers risk

  Opportunity inputs:
    expected_roi         float  – 0–∞; log-normalized; default 0.0
    urgency              float  – 0–1; higher = sooner deadline; default 0.0
    strategic_alignment  str    – "none"|"low"|"medium"|"high"|"critical"; default "medium"
    resource_intensity   float  – 0–1; 1 = most resource-intensive; default 0.3
    execution_risk       float  – 0–1; subjective execution difficulty; default 0.3
"""

from __future__ import annotations

import math

from .config import DecisionEngineSettings
from .constants import DECISION_MATRIX, opportunity_bucket
from .models import DecisionContext, DecisionOutcome, RiskBand, ScoringResult

# ---------------------------------------------------------------------------
# Event types that are irreversible by nature (reversibility_penalty = 1.0)
# ---------------------------------------------------------------------------
_IRREVERSIBLE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "sales.campaign_launched",
        "sales.budget_committed",
        "sales.contract_signed",
        "creative.content_published",
        "creative.ad_submitted",
        "sales.proposal_accepted",
    }
)

# ---------------------------------------------------------------------------
# Strategic-alignment multipliers (0–1 scale)
# ---------------------------------------------------------------------------
_ALIGNMENT_MULTIPLIERS: dict[str, float] = {
    "none": 0.0,
    "low": 0.25,
    "medium": 0.50,
    "high": 0.80,
    "critical": 1.0,
}


def _time_decay_factor(urgency: float) -> float:
    """Concave urgency curve: f(u) = 1 − (1 − u)², maps [0,1] → [0,1]."""
    u = max(0.0, min(1.0, urgency))
    return 1.0 - (1.0 - u) ** 2


def _normalize(value: float, low: float, high: float) -> float:
    """Min-max normalize `value` from [low, high] to [0, 100], clamped."""
    if high == low:
        return 0.0
    return max(0.0, min(1.0, (value - low) / (high - low))) * 100.0


class ScoringEngine:
    """
    Deterministic scoring engine for Decision Engine STAGE 3.

    Default weights and bounds (all configurable; currently hardcoded as
    class-level constants pending weight fields in DecisionEngineSettings):

    Risk formula:
        raw = (downside_magnitude × probability_weight)
              + reversibility_penalty
              + data_confidence_penalty
              − hedge_factor
        where:
          downside_magnitude    ∈ [0, 1]
          probability_weight    = historical_failure_rate ∈ [0, 1]
          reversibility_penalty ∈ {0.0, 1.0}  (binary)
          data_confidence_penalty = 1 − payload.confidence  ∈ [0, 1]
          hedge_factor          ∈ {0.0, 0.5}  (presence of mitigation_plan)
        raw range: [−0.5, 3.0]; normalized to 0–100.

    Opportunity formula:
        base = expected_roi_norm × strategic_alignment_multiplier
        raw  = (base + base × time_decay_factor × W_URGENCY)
               − resource_intensity × W_RESOURCE
               − execution_risk     × W_EXEC_RISK
        Clamped to [0, 1] then × 100.
        Weights: W_URGENCY=0.20, W_RESOURCE=0.35, W_EXEC_RISK=0.45.

    ScoringResult.confidence = average of data_confidence and (1 − execution_risk).
    """

    _RISK_RAW_MIN: float = -0.5
    _RISK_RAW_MAX: float = 3.0

    _OPP_W_URGENCY: float = 0.20
    _OPP_W_RESOURCE: float = 0.35
    _OPP_W_EXEC_RISK: float = 0.45

    def __init__(self, settings: DecisionEngineSettings) -> None:
        self._settings = settings

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def score(self, context: DecisionContext) -> ScoringResult:
        """Compute full ScoringResult for a decision context."""
        risk_score, risk_factors = self.compute_risk_score(context)
        opp_score, opp_factors = self.compute_opportunity_score(context)
        risk_band = self.compute_risk_band(risk_score)

        data_confidence = max(0.0, min(1.0, float(context.payload.get("confidence", 0.7))))
        exec_risk = max(0.0, min(1.0, float(context.payload.get("execution_risk", 0.3))))
        combined_confidence = (data_confidence + (1.0 - exec_risk)) / 2.0

        return ScoringResult(
            risk_score=round(risk_score, 4),
            opportunity_score=round(opp_score, 4),
            risk_band=risk_band,
            confidence=round(combined_confidence, 4),
            factors={**risk_factors, **opp_factors},
        )

    def compute_risk_score(self, context: DecisionContext) -> tuple[float, dict[str, float]]:
        """
        Return (risk_score_0_to_100, factors_dict).

        Formula:
            raw = (downside_magnitude × probability_weight)
                  + reversibility_penalty
                  + data_confidence_penalty
                  − hedge_factor
        Normalized over [_RISK_RAW_MIN, _RISK_RAW_MAX] → 0–100.
        """
        payload = context.payload

        # downside_magnitude: 0–1
        if "severity" in payload:
            downside = max(0.0, min(1.0, float(payload["severity"])))
        else:
            requested = float(payload.get("requested_amount", 0.0))
            ceiling = float(payload.get("budget_ceiling", 1.0)) or 1.0
            downside = min(1.0, requested / ceiling) if requested > 0 else 0.0

        # probability_weight: historical_failure_rate ∈ [0, 1], default 0.3
        prob_weight = max(0.0, min(1.0, float(payload.get("historical_failure_rate", 0.3))))

        # reversibility_penalty: binary 0 or 1
        if "reversible" in payload:
            rev_penalty = 0.0 if payload["reversible"] else 1.0
        else:
            rev_penalty = 1.0 if context.event_type in _IRREVERSIBLE_EVENT_TYPES else 0.0

        # data_confidence_penalty: 1 − confidence, default confidence = 0.7
        data_confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.7))))
        conf_penalty = 1.0 - data_confidence

        # hedge_factor: 0.5 if mitigation_plan present, else 0
        hedge = 0.5 if payload.get("mitigation_plan") is not None else 0.0

        raw = (downside * prob_weight) + rev_penalty + conf_penalty - hedge
        score = _normalize(raw, self._RISK_RAW_MIN, self._RISK_RAW_MAX)

        factors: dict[str, float] = {
            "risk.downside_magnitude": round(downside, 4),
            "risk.probability_weight": round(prob_weight, 4),
            "risk.reversibility_penalty": round(rev_penalty, 4),
            "risk.data_confidence_penalty": round(conf_penalty, 4),
            "risk.hedge_factor": round(hedge, 4),
            "risk.raw": round(raw, 4),
        }
        return score, factors

    def compute_opportunity_score(
        self, context: DecisionContext
    ) -> tuple[float, dict[str, float]]:
        """
        Return (opportunity_score_0_to_100, factors_dict).

        Formula:
            base         = expected_roi_norm × strategic_alignment_multiplier
            urgency_bonus = base × time_decay_factor × W_URGENCY
            raw          = (base + urgency_bonus)
                           − resource_intensity × W_RESOURCE
                           − execution_risk     × W_EXEC_RISK
        Clamped [0, 1] then × 100.

        expected_roi is log-normalized: log1p(roi) / log1p(9), so roi=0→0,
        roi=1→≈0.5, roi=9→1.0; values beyond 9 are clamped.
        """
        payload = context.payload

        raw_roi = max(0.0, float(payload.get("expected_roi", 0.0)))
        roi_norm = min(1.0, math.log1p(raw_roi) / math.log1p(9.0))

        alignment_key = str(payload.get("strategic_alignment", "medium")).lower()
        alignment = _ALIGNMENT_MULTIPLIERS.get(alignment_key, 0.5)

        urgency = max(0.0, min(1.0, float(payload.get("urgency", 0.0))))
        tdf = _time_decay_factor(urgency)

        resource_intensity = max(0.0, min(1.0, float(payload.get("resource_intensity", 0.3))))
        execution_risk = max(0.0, min(1.0, float(payload.get("execution_risk", 0.3))))

        base = roi_norm * alignment
        urgency_bonus = base * tdf * self._OPP_W_URGENCY
        raw = (
            (base + urgency_bonus)
            - resource_intensity * self._OPP_W_RESOURCE
            - execution_risk * self._OPP_W_EXEC_RISK
        )
        score = max(0.0, min(1.0, raw)) * 100.0

        factors: dict[str, float] = {
            "opp.expected_roi_norm": round(roi_norm, 4),
            "opp.strategic_alignment": round(alignment, 4),
            "opp.time_decay_factor": round(tdf, 4),
            "opp.resource_intensity": round(resource_intensity, 4),
            "opp.execution_risk": round(execution_risk, 4),
            "opp.base": round(base, 4),
            "opp.urgency_bonus": round(urgency_bonus, 4),
            "opp.raw": round(raw, 4),
        }
        return score, factors

    # -----------------------------------------------------------------------
    # Classifiers
    # -----------------------------------------------------------------------

    @staticmethod
    def compute_risk_band(risk_score: float) -> RiskBand:
        """Map 0–100 risk score to RiskBand.

        Thresholds (inclusive lower, exclusive upper except CRITICAL):
          0–30   → LOW
          31–60  → MED
          61–85  → HIGH
          86–100 → CRITICAL
        """
        if risk_score <= 30.0:
            return RiskBand.LOW
        if risk_score <= 60.0:
            return RiskBand.MED
        if risk_score <= 85.0:
            return RiskBand.HIGH
        return RiskBand.CRITICAL

    @staticmethod
    def lookup_matrix(risk_band: RiskBand, opp_score: float) -> DecisionOutcome:
        """Resolve (risk_band, opportunity_score) → DecisionOutcome.

        CRITICAL is not in the 3×3 DECISION_MATRIX (always DEFERRED_TO_HUMAN).
        All other cells delegate to constants.DECISION_MATRIX.
        """
        if risk_band is RiskBand.CRITICAL:
            return DecisionOutcome.DEFERRED_TO_HUMAN
        return DECISION_MATRIX[(risk_band, opportunity_bucket(opp_score))]
