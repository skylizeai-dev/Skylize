from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel


class EvaluationStage(str, Enum):
    AUTHORITY = "AUTHORITY"
    OPA_POLICY = "OPA_POLICY"
    SCORING = "SCORING"
    CAPITAL = "CAPITAL"
    CONFLICT = "CONFLICT"
    HITL_GATE = "HITL_GATE"


class DecisionOutcome(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DEFERRED_TO_HUMAN = "DEFERRED_TO_HUMAN"
    ESCALATED = "ESCALATED"


class RiskBand(str, Enum):
    LOW = "LOW"
    MED = "MED"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ScoringResult(BaseModel):
    risk_score: float
    opportunity_score: float
    risk_band: RiskBand
    confidence: float
    factors: dict[str, float]


class CapitalCheckResult(BaseModel):
    available_budget: Decimal
    requested_amount: Decimal
    ceiling_pct: float
    passes: bool
    reason: str


class EvaluationStepRecord(BaseModel):
    stage: EvaluationStage
    passed: bool
    outcome: DecisionOutcome | None
    detail: dict
    duration_ms: float
    timestamp: datetime


class DecisionContext(BaseModel):
    event_id: str
    tenant_id: str
    department: str
    event_type: str
    payload: dict
    received_at: datetime
    steps: list[EvaluationStepRecord] = []


class DecisionResult(BaseModel):
    decision_id: str
    event_id: str
    tenant_id: str
    outcome: DecisionOutcome
    scoring: ScoringResult | None
    capital: CapitalCheckResult | None
    final_reason: str
    steps: list[EvaluationStepRecord]
    evaluated_at: datetime
