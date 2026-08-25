"""
Decision Engine domain models and the inbound-event → proposal mapping.

These are the engine's *internal* contracts — distinct from the wire
`DecisionEvent` schemas in `schemas/events/decision.py` that the engine emits.
A `DecisionProposal` is the normalized unit of work the six evaluation stages
consume; `DecisionResult` (with `DecisionScore` and `Conflict`) is what they
produce. The engine then projects a `DecisionResult` onto the wire events.

`DecisionProposal.from_event` is the one place that knows how each spend- or
launch-bearing business event maps onto the common proposal shape; every other
part of the engine works against `DecisionProposal` only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from ...schemas.agents.safety import SafetyVerdictOut
from ...schemas.base import AuthorityLevelLiteral, BaseEvent
from ...schemas.events.creative import CreativeReviewRequested
from ...schemas.events.sales import (
    SalesBudgetReallocationProposed,
    SalesCampaignProposed,
)

# Deterministic id derivation: the same source event always yields the same
# decision_id / hitl_id, which is what makes replay and idempotency stable.
_DECISION_NS = UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

# The synchronous agent-execution vertical's action kind. A first-class
# KNOWN_ACTION_KINDS member (owner decision K2): the request-path gate
# (POST /api/v1/agents/execute) builds a proposal with this kind, so policy_check
# must recognize it rather than reject it as an unknown action class. This is an
# authorized enum extension, not an invented value.
AGENT_EXECUTE_ACTION_KIND = "agent.execute"

# The action classes the engine knows how to evaluate. An unknown class is never
# guessed — policy stage rejects it (decision_engine.md §6).
KNOWN_ACTION_KINDS: frozenset[str] = frozenset(
    {
        "creative.review",
        "sales.campaign",
        "sales.budget_reallocation",
        AGENT_EXECUTE_ACTION_KIND,
    }
)

DecisionOutcome = Literal["approved", "rejected", "deferred_to_human"]

# The rules the conflict resolver may apply, in precedence order. `safety_veto`
# outranks authority/recency: a safety-rejected proposal cannot win a conflict
# whatever its authority. `escalated` never reaches the wire (its winner is None).
RuleApplied = Literal["authority", "recency", "safety_veto", "escalated"]


def decision_id_for(proposal_id: UUID) -> UUID:
    """Deterministic decision_id derived from the source proposal id."""
    return uuid5(_DECISION_NS, f"decision:{proposal_id}")


def hitl_id_for(proposal_id: UUID) -> UUID:
    """Deterministic HITL ticket id derived from the source proposal id."""
    return uuid5(_DECISION_NS, f"hitl:{proposal_id}")


class SecurityVerdict(BaseModel):
    """A Safety Suite verdict carried onto a proposal as a typed sibling field.

    A safety signal is load-bearing — it can veto a conflict — so it gets its own
    field rather than being smuggled through the free-form `metadata` bag.
    `reject` is the agent's own safe/unsafe call inverted; `severity` and
    `reason` are carried verbatim for the audit trail. Deciding *which*
    severities should reject is policy and lives in the evaluator/guardrails, not
    here — this model only transports the verdict.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_agent_id: str
    reject: bool
    severity: str  # mirrors SafetyVerdictOut.severity ('none'..'critical')
    reason: str

    @classmethod
    def from_safety_verdict(
        cls, verdict: SafetyVerdictOut, *, source_agent_id: str
    ) -> SecurityVerdict:
        """Map a Safety Suite `SafetyVerdictOut` onto the proposal carrier.

        `reject` is the direct inverse of the agent's `safe` flag — a field
        rename, not a threshold (applying a threshold would be policy)."""
        return cls(
            source_agent_id=source_agent_id,
            reject=not verdict.safe,
            severity=verdict.severity,
            reason="; ".join(verdict.findings) if verdict.findings else "",
        )


class DecisionProposal(BaseModel):
    """Normalized intent the evaluator decides on, mapped from a business event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: UUID  # == source event_id (idempotency anchor)
    correlation_id: UUID
    partition_key: str  # ordering / conflict key (one mandate per partition)
    org_id: str
    department: str
    proposing_agent_id: str
    governance_token_id: UUID | None = None

    action_kind: str  # e.g. "creative.review" | "sales.campaign"
    requires_external_launch: bool = False
    spend_minor_units: int | None = None
    currency: str | None = None
    capital_scope: str = "org"  # ledger scope for the capital check

    occurred_at: datetime
    source_event_id: UUID
    source_type: str
    # Typed safety signal from the Safety Suite. `None` means "no safety signal
    # present" — which is NOT an implicit allow and NOT an implicit reject; only
    # a verdict with reject=True vetoes (see DecisionEvaluator._resolve).
    security_verdict: SecurityVerdict | None = None
    # Free-form signals the HITL / policy stages match against (brand_sensitive,
    # security_severity, confidence, irreversible, …). Hashes only ever leave the
    # engine via the audit trail.
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def involves_spend(self) -> bool:
        return self.spend_minor_units is not None

    @classmethod
    def from_event(cls, event: BaseEvent) -> DecisionProposal | None:
        """Map a consumed business event to a proposal, or None if it is not
        a decision-bearing event (the engine ignores those)."""
        if isinstance(event, CreativeReviewRequested):
            p = event.payload
            return cls(
                proposal_id=event.event_id,
                correlation_id=event.correlation_id,
                partition_key=event.partition_key,
                org_id=event.tenant_id,
                department=event.department,
                proposing_agent_id=event.source_agent_id or "",
                governance_token_id=event.governance_token_id,
                action_kind="creative.review",
                requires_external_launch=p.proposed_action == "launch",
                spend_minor_units=p.proposed_spend_minor_units,
                currency=None,
                capital_scope=event.department,
                occurred_at=event.occurred_at,
                source_event_id=event.event_id,
                source_type=event.type,
                metadata={
                    "proposed_action": p.proposed_action,
                    "brief_id": str(p.brief_id),
                },
            )
        if isinstance(event, SalesCampaignProposed):
            p2 = event.payload
            return cls(
                proposal_id=event.event_id,
                correlation_id=event.correlation_id,
                partition_key=event.partition_key,
                org_id=event.tenant_id,
                department=event.department,
                proposing_agent_id=event.source_agent_id or "",
                governance_token_id=event.governance_token_id,
                action_kind="sales.campaign",
                requires_external_launch=True,  # launching on meta/tiktok is external
                spend_minor_units=p2.proposed_budget_minor_units,
                currency=p2.currency,
                capital_scope=event.department,
                occurred_at=event.occurred_at,
                source_event_id=event.event_id,
                source_type=event.type,
                metadata={"campaign_id": p2.campaign_id, "channel": p2.channel},
            )
        if isinstance(event, SalesBudgetReallocationProposed):
            p3 = event.payload
            return cls(
                proposal_id=event.event_id,
                correlation_id=event.correlation_id,
                partition_key=event.partition_key,
                org_id=event.tenant_id,
                department=event.department,
                proposing_agent_id=event.source_agent_id or "",
                governance_token_id=event.governance_token_id,
                action_kind="sales.budget_reallocation",
                requires_external_launch=False,  # internal move between scopes
                spend_minor_units=p3.amount_minor_units,
                currency=p3.currency,
                capital_scope=p3.to_scope,
                occurred_at=event.occurred_at,
                source_event_id=event.event_id,
                source_type=event.type,
                metadata={"from_scope": p3.from_scope, "to_scope": p3.to_scope},
            )
        return None


class DecisionScore(BaseModel):
    """Deterministic 0–100 score with its component breakdown (scoring_models.md)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: int = Field(ge=0, le=100)
    components: dict[str, float]
    rationale: str


class Conflict(BaseModel):
    """A collision between two proposals on the same partition_key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    partition_key: str
    proposal_ids: list[UUID]
    rule_applied: RuleApplied
    winning_proposal_id: UUID | None


class DecisionResult(BaseModel):
    """The evaluator's verdict — one terminal outcome plus the full evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: UUID
    decision_id: UUID
    proposing_agent: str
    action_kind: str
    outcome: DecisionOutcome
    stages_completed: list[str]
    stage_failed_at: str | None = None
    reasons: list[str] = Field(default_factory=list)
    score: DecisionScore | None = None
    hitl_trigger: str | None = None
    routed_to: str | None = None
    conflicts: list[Conflict] = Field(default_factory=list)
    policy_version: str | None = None
    authority_level: AuthorityLevelLiteral | None = None
