"""Creative crew agent I/O models (MVP).

Each model is referenced by dotted path in an AgentContract, e.g.
`skylize.schemas.agents.creative.HookRequestIn`. Business logic that produces
these is implemented in later sprints; here we fix the contract shapes.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── VP Creative ──────────────────────────────────────────────────────────────
class CreativeMandateIn(_Base):
    mandate: str
    campaign_id: str | None = None
    constraints: dict[str, str] = Field(default_factory=dict)


class CreativeStrategyOut(_Base):
    strategy: str
    delegated_briefs: list[str]


# ── Copy Director ────────────────────────────────────────────────────────────
class CopyBriefIn(_Base):
    brief_id: UUID
    product: str
    audience: str
    angle: str | None = None


class CopyPackageOut(_Base):
    brief_id: UUID
    hooks: list[str]
    body_copy: list[str]
    ctas: list[str]


# ── Art Director ─────────────────────────────────────────────────────────────
class ArtBriefIn(_Base):
    brief_id: UUID
    concept: str
    format: str  # 'static' | 'video' | 'carousel'


class ArtPackageOut(_Base):
    brief_id: UUID
    asset_descriptions: list[str]


# ── Creative Operations Manager ──────────────────────────────────────────────
class OpsTaskIn(_Base):
    brief_id: UUID
    task_kind: str


class OpsStatusOut(_Base):
    brief_id: UUID
    routed_to: list[str]
    status: str


# ── Hook Generator (worker) ──────────────────────────────────────────────────
class HookRequestIn(_Base):
    brief_id: UUID
    product: str
    audience: str
    count: int = 3


class HooksOut(_Base):
    brief_id: UUID
    hooks: list[str]


# Operator-execute variant (`/api/v1/agents/execute` + the creative workflow):
# a human supplies brand context instead of an upstream brief, so there is no
# brief_id to echo.
class HookGeneratorExecuteIn(_Base):
    brand_name: str
    product_description: str
    target_audience: str
    tone: str | None = None
    count: int = Field(default=3, ge=1, le=10)


class HookGeneratorExecuteOut(_Base):
    hooks: list[str]


# ── Ad Copy (worker) ─────────────────────────────────────────────────────────
class AdCopyRequestIn(_Base):
    brief_id: UUID
    hook: str
    product: str


class AdCopyOut(_Base):
    brief_id: UUID
    variants: list[str]


# ── Caption Writer (worker) ──────────────────────────────────────────────────
class CaptionRequestIn(_Base):
    brief_id: UUID
    asset_description: str
    channel: str


class CaptionsOut(_Base):
    brief_id: UUID
    captions: list[str]


# ── Script Writer (worker) ───────────────────────────────────────────────────
class ScriptRequestIn(_Base):
    brief_id: UUID
    concept: str
    duration_seconds: int


class ScriptOut(_Base):
    brief_id: UUID
    script: str
    beats: list[str]


# ── CTA Optimizer (worker) ───────────────────────────────────────────────────
class CTARequestIn(_Base):
    brief_id: UUID
    offer: str


class CTAOut(_Base):
    brief_id: UUID
    ctas: list[str]
