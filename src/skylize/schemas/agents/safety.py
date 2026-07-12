"""Safety Suite agent I/O models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SafetyAssessmentIn(_Base):
    run_id: str
    agent_id: str
    output_text: str


class SafetyVerdictOut(_Base):
    run_id: str
    safe: bool
    severity: str  # 'none' | 'low' | 'medium' | 'high' | 'critical'
    findings: list[str]
