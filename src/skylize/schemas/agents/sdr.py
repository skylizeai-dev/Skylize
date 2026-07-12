"""SDR agent I/O models (MVP: `sdr_outreach_agent`, `lead_qualifier_agent`)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SDROutreachInput(_Base):
    lead_id: str
    lead_name: str
    company: str
    context: dict[str, str] = {}
    sequence_step: int = 1


class SDROutreachOutput(_Base):
    lead_id: str
    message: str
    channel: str  # 'email' | 'linkedin' | 'call'
    sent: bool
    notes: str = ""


class LeadQualifierInput(_Base):
    lead_id: str
    lead_data: dict[str, str]
    icp_criteria: dict[str, str] = {}


class LeadQualifierOutput(_Base):
    lead_id: str
    qualified: bool
    score: float  # 0.0 - 1.0
    reasons: list[str]
    recommended_action: str
