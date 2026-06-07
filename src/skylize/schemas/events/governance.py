"""GovernanceEvent — `category=governance` (event_driven_architecture.md §5).

Owner: Governance Authority. Records token lifecycle, scope violations,
circuit-breaker trips, suspension/reinstatement, and kill-switch transitions.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ..base import BaseEvent, EventCategory


class GovernanceTokenIssued(BaseEvent):
    category: Literal[EventCategory.GOVERNANCE] = EventCategory.GOVERNANCE
    type: Literal["governance.token_issued"] = "governance.token_issued"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        token_id: UUID
        agent_id: str
        authority_level: str
        scope: list[str]
        expires_at: str  # ISO 8601

    payload: Payload


class GovernanceTokenRevoked(BaseEvent):
    category: Literal[EventCategory.GOVERNANCE] = EventCategory.GOVERNANCE
    type: Literal["governance.token_revoked"] = "governance.token_revoked"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        token_id: UUID
        agent_id: str
        reason: str

    payload: Payload


class GovernanceScopeViolation(BaseEvent):
    category: Literal[EventCategory.GOVERNANCE] = EventCategory.GOVERNANCE
    type: Literal["governance.scope_violation"] = "governance.scope_violation"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        token_id: UUID
        agent_id: str
        attempted_tool: str
        failed_stage: str  # signature|expiry|revocation|scope|budget|delegation
        reason: str

    payload: Payload


class GovernanceCircuitBreakerTripped(BaseEvent):
    category: Literal[EventCategory.GOVERNANCE] = EventCategory.GOVERNANCE
    type: Literal["governance.circuit_breaker_tripped"] = "governance.circuit_breaker_tripped"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        agent_id: str
        trip_reason: str
        trip_count: int

    payload: Payload


class GovernanceAgentSuspended(BaseEvent):
    category: Literal[EventCategory.GOVERNANCE] = EventCategory.GOVERNANCE
    type: Literal["governance.agent_suspended"] = "governance.agent_suspended"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        agent_id: str
        reason: str

    payload: Payload


class GovernanceAgentReinstated(BaseEvent):
    category: Literal[EventCategory.GOVERNANCE] = EventCategory.GOVERNANCE
    type: Literal["governance.agent_reinstated"] = "governance.agent_reinstated"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        agent_id: str
        reinstated_by: str

    payload: Payload


class GovernanceKillSwitchEngaged(BaseEvent):
    category: Literal[EventCategory.GOVERNANCE] = EventCategory.GOVERNANCE
    type: Literal["governance.kill_switch_engaged"] = "governance.kill_switch_engaged"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        scope_type: str  # agent|department|tenant|platform
        scope_id: str
        engaged_by: str
        reason: str

    payload: Payload


class GovernanceKillSwitchDisengaged(BaseEvent):
    category: Literal[EventCategory.GOVERNANCE] = EventCategory.GOVERNANCE
    type: Literal["governance.kill_switch_disengaged"] = "governance.kill_switch_disengaged"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        scope_type: str
        scope_id: str
        disengaged_by: str

    payload: Payload
