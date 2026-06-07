"""CreativeEvent — `category=creative` (event_driven_architecture.md §5)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ..base import BaseEvent, EventCategory


class CreativeHooksGenerated(BaseEvent):
    category: Literal[EventCategory.CREATIVE] = EventCategory.CREATIVE
    type: Literal["creative.hooks_generated"] = "creative.hooks_generated"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        brief_id: UUID
        hooks: list[str]
        model_used: str
        token_cost: int

    payload: Payload


class CreativeCopyDrafted(BaseEvent):
    category: Literal[EventCategory.CREATIVE] = EventCategory.CREATIVE
    type: Literal["creative.copy_drafted"] = "creative.copy_drafted"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        brief_id: UUID
        copy_variant: str
        word_count: int
        model_used: str
        token_cost: int

    payload: Payload


class CreativeReviewRequested(BaseEvent):
    category: Literal[EventCategory.CREATIVE] = EventCategory.CREATIVE
    type: Literal["creative.review_requested"] = "creative.review_requested"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        brief_id: UUID
        asset_ids: list[UUID]
        proposed_action: str  # 'launch' | 'stage' | 'approve_internal'
        proposed_spend_minor_units: int | None = None  # None when no spend

    payload: Payload


class CreativeAssetApproved(BaseEvent):
    category: Literal[EventCategory.CREATIVE] = EventCategory.CREATIVE
    type: Literal["creative.asset_approved"] = "creative.asset_approved"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        brief_id: UUID
        asset_ids: list[UUID]
        approved_by: str  # agent_id or user_id

    payload: Payload


class CreativeAssetRejected(BaseEvent):
    category: Literal[EventCategory.CREATIVE] = EventCategory.CREATIVE
    type: Literal["creative.asset_rejected"] = "creative.asset_rejected"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        brief_id: UUID
        asset_ids: list[UUID]
        reason: str
        rejected_by: str

    payload: Payload
