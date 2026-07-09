"""Agency ops agent I/O models (MVP: `agency_requirements_analyst`, `agency_deliverable_drafter`)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequirementsAnalystInput(_Base):
    client_id: str
    project_id: str
    raw_brief: str
    context: dict[str, str] = {}


class RequirementsAnalystOutput(_Base):
    client_id: str
    project_id: str
    structured_requirements: list[str]
    open_questions: list[str]
    confidence: float  # 0.0 - 1.0


class DeliverableDrafterInput(_Base):
    client_id: str
    project_id: str
    requirements: list[str]
    template_id: str = ""
    context: dict[str, str] = {}


class DeliverableDrafterOutput(_Base):
    client_id: str
    project_id: str
    deliverable_title: str
    content: str
    revision_notes: str = ""
