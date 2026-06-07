"""Growth agent I/O models (MVP: director_growth).

The growth half of the "creative + growth" MVP: produces campaign proposals
that the Decision Engine evaluates (Mode C — decision request).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GrowthMandateIn(_Base):
    objective: str
    channel: str  # 'meta' | 'tiktok'
    target_budget_minor_units: int
    currency: str = "USD"


class CampaignProposalOut(_Base):
    campaign_id: str
    channel: str
    proposed_budget_minor_units: int
    currency: str
    objective: str
    rationale: str
