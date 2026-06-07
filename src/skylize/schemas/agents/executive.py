"""Executive agent I/O models (MVP: `ceo`)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrategicDirectiveIn(_Base):
    directive: str
    horizon: str | None = None
    constraints: dict[str, str] = Field(default_factory=dict)


class StrategicDecisionOut(_Base):
    decision: str
    delegated_mandates: list[str]
    rationale: str
