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
    """Breaker trip across any trip condition in agent_governance.md §7.

    ``trip_reason`` is free-text and identifies the condition: scope-violation
    threshold (the originating violation reason), or ``"convergence: ..."`` when an
    agent repeats the same action twice consecutively within a workflow (runaway
    loop — see ``app/governance/authority.record_action``). No new event type is
    needed for convergence; it reuses this event with a ``convergence`` reason,
    keeping the event taxonomy closed (event_driven_architecture.md §5).
    """

    category: Literal[EventCategory.GOVERNANCE] = EventCategory.GOVERNANCE
    type: Literal["governance.circuit_breaker_tripped"] = "governance.circuit_breaker_tripped"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        agent_id: str
        trip_reason: str  # e.g. a scope-violation reason, or "convergence: <detail>"
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


class GovernanceHumanApprovalReceived(BaseEvent):
    """A human verdict on a HITL-deferred decision. Consumed by the Decision
    Engine to resume a paused decision into its terminal approve/reject outcome."""

    category: Literal[EventCategory.GOVERNANCE] = EventCategory.GOVERNANCE
    type: Literal["governance.human_approval_received"] = "governance.human_approval_received"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        decision_id: UUID
        hitl_id: UUID
        approved: bool
        decided_by: str
        # Optional human rationale; surfaced in decision.rejected reasons and
        # the audit record on a rejection resume.
        reason: str | None = None

    payload: Payload
