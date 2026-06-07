"""Brand crew agent I/O models (MVP: brand_guardian, tone_of_voice)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BrandCheckIn(_Base):
    brief_id: UUID
    content: str
    content_kind: str  # 'hook' | 'copy' | 'caption' | 'script'


class BrandVerdictOut(_Base):
    brief_id: UUID
    outcome: str  # 'approve' | 'reject'
    violations: list[str]
    confidence: float


class ToneCheckIn(_Base):
    brief_id: UUID
    content: str


class ToneAdjustedOut(_Base):
    brief_id: UUID
    adjusted_content: str
    notes: list[str]
