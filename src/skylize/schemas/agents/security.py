"""Security agent I/O models (MVP: `fraud_detection_agent`)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActivitySignalIn(_Base):
    entity_id: str
    signal_kind: str
    features: dict[str, float]


class FraudVerdictOut(_Base):
    entity_id: str
    outcome: str  # 'allow' | 'reject' | 'review'
    confidence: float  # 0.0 - 1.0
    reasons: list[str]
