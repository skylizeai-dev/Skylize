"""DecisionEvent — `category=decision` (event_driven_architecture.md §5).

Owner: Decision Engine. Exactly one terminal outcome per proposal
(`approved` / `rejected` / `deferred_to_human`), preceded by `evaluated`.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ..base import BaseEvent, EventCategory


class DecisionEvaluated(BaseEvent):
    category: Literal[EventCategory.DECISION] = EventCategory.DECISION
    type: Literal["decision.evaluated"] = "decision.evaluated"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        decision_id: UUID
        proposing_agent: str
        action_kind: str
        stages_completed: list[str]
        policy_version: str | None = None

    payload: Payload


class DecisionApproved(BaseEvent):
    category: Literal[EventCategory.DECISION] = EventCategory.DECISION
    type: Literal["decision.approved"] = "decision.approved"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        decision_id: UUID
        action_kind: str
        approved_scope: dict[str, str]

    payload: Payload


class DecisionRejected(BaseEvent):
    category: Literal[EventCategory.DECISION] = EventCategory.DECISION
    type: Literal["decision.rejected"] = "decision.rejected"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        decision_id: UUID
        action_kind: str
        stage_rejected_at: str
        reasons: list[str]
        policy_version: str | None = None

    payload: Payload


class DecisionDeferredToHuman(BaseEvent):
    category: Literal[EventCategory.DECISION] = EventCategory.DECISION
    type: Literal["decision.deferred_to_human"] = "decision.deferred_to_human"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        decision_id: UUID
        hitl_id: UUID
        trigger_reason: str  # HumanInLoopTrigger value
        routed_to: str  # user_id or role

    payload: Payload


class DecisionConflictDetected(BaseEvent):
    category: Literal[EventCategory.DECISION] = EventCategory.DECISION
    type: Literal["decision.conflict_detected"] = "decision.conflict_detected"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        partition_key: str
        proposal_ids: list[UUID]

    payload: Payload


class DecisionConflictResolved(BaseEvent):
    category: Literal[EventCategory.DECISION] = EventCategory.DECISION
    type: Literal["decision.conflict_resolved"] = "decision.conflict_resolved"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        partition_key: str
        winning_proposal_id: UUID
        # A resolved conflict always has a winner, so `escalated` (winner=None)
        # never reaches the wire — but it is kept in the enum for parity with the
        # engine's Conflict.rule_applied domain type.
        rule_applied: Literal["authority", "recency", "safety_veto", "escalated"]

    payload: Payload
