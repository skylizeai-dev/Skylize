"""SalesEvent — `category=sales` (event_driven_architecture.md §5).

Departments: Sales Intelligence, Growth. Spend-bearing proposals
(`campaign_proposed`, `budget_reallocation_proposed`) are consumed by the
Decision Engine; nothing here executes spend on its own.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..base import BaseEvent, EventCategory


class SalesLeadEnriched(BaseEvent):
    category: Literal[EventCategory.SALES] = EventCategory.SALES
    type: Literal["sales.lead_enriched"] = "sales.lead_enriched"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        lead_id: str
        enrichment: dict[str, str]

    payload: Payload


class SalesSignalDetected(BaseEvent):
    category: Literal[EventCategory.SALES] = EventCategory.SALES
    type: Literal["sales.signal_detected"] = "sales.signal_detected"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        signal_kind: str
        entity_id: str
        strength: float

    payload: Payload


class SalesCampaignProposed(BaseEvent):
    category: Literal[EventCategory.SALES] = EventCategory.SALES
    type: Literal["sales.campaign_proposed"] = "sales.campaign_proposed"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        campaign_id: str
        channel: str  # 'meta' | 'tiktok'
        proposed_budget_minor_units: int
        currency: str
        objective: str

    payload: Payload


class SalesCampaignLaunched(BaseEvent):
    category: Literal[EventCategory.SALES] = EventCategory.SALES
    type: Literal["sales.campaign_launched"] = "sales.campaign_launched"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        campaign_id: str
        channel: str
        external_campaign_ref: str  # provider-side id
        committed_minor_units: int

    payload: Payload


class SalesPerformanceIngested(BaseEvent):
    category: Literal[EventCategory.SALES] = EventCategory.SALES
    type: Literal["sales.performance_ingested"] = "sales.performance_ingested"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        campaign_id: str
        channel: str
        spend_minor_units: int
        impressions: int
        clicks: int
        conversions: int
        roas: float

    payload: Payload


class SalesBudgetReallocationProposed(BaseEvent):
    category: Literal[EventCategory.SALES] = EventCategory.SALES
    type: Literal["sales.budget_reallocation_proposed"] = "sales.budget_reallocation_proposed"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        from_scope: str
        to_scope: str
        amount_minor_units: int
        currency: str
        rationale: str

    payload: Payload
