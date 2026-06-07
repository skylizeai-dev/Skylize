"""AuditEvent — `category=audit` (event_driven_architecture.md §5).

Owner: Audit subsystem. Immutable, append-only — the compliance spine. Every
other category's significant transitions are mirrored here, so the audit stream
alone can reconstruct system history.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..base import BaseEvent, EventCategory


class AuditActionRecorded(BaseEvent):
    category: Literal[EventCategory.AUDIT] = EventCategory.AUDIT
    type: Literal["audit.action_recorded"] = "audit.action_recorded"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        action_type: str
        inputs_hash: str | None = None  # PII-safe
        outputs_hash: str | None = None  # PII-safe
        result: str  # success|denied|escalated|failed
        result_reason: str | None = None

    payload: Payload


class AuditAccessDenied(BaseEvent):
    category: Literal[EventCategory.AUDIT] = EventCategory.AUDIT
    type: Literal["audit.access_denied"] = "audit.access_denied"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        resource: str  # namespace or table
        reason: str
        cross_tenant: bool = False

    payload: Payload


class AuditDataAccess(BaseEvent):
    category: Literal[EventCategory.AUDIT] = EventCategory.AUDIT
    type: Literal["audit.data_access"] = "audit.data_access"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        resource: str
        operation: str  # read|write
        record_count: int

    payload: Payload


class AuditSchemaRejected(BaseEvent):
    category: Literal[EventCategory.AUDIT] = EventCategory.AUDIT
    type: Literal["audit.schema_rejected"] = "audit.schema_rejected"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        rejected_type: str | None = None
        rejected_schema_version: str | None = None
        reason: str

    payload: Payload


class AuditReplayExecuted(BaseEvent):
    category: Literal[EventCategory.AUDIT] = EventCategory.AUDIT
    type: Literal["audit.replay_executed"] = "audit.replay_executed"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        mode: str  # shadow|authoritative
        selector: str  # the replay scope description
        approved_by: str | None = None  # required for authoritative

    payload: Payload
