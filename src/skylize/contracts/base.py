"""
The AgentContract and GovernanceToken models.

CONSISTENCY CONTRACT (agent_governance.md §12, agent_contract_registry.md §6):
  - `AuthorityLevel` is the canonical set `executive/vp/director/manager/worker`,
    identical to the literal in schemas/base.py.
  - `GovernanceToken` here is the single, byte-identical definition used
    everywhere; it is signed with ECDSA P-384 (CB-1 resolved).
  - `escalation_path` is an ordered chain up the org tree ending at
    `human_owner`.

These models are frozen and `extra="forbid"`: nothing about an agent is
implicit. If it is not in the contract, the agent cannot do it.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Canonical authority levels — IDENTICAL to agent_governance.md §2.
AuthorityLevel = Literal["executive", "vp", "director", "manager", "worker"]

# Schema version of the SIGNED token payload.
#   "1.0" — the original eleven-field token. Its canonical bytes are frozen
#           forever by tests/contract/test_token_v10_backcompat.py.
#   "1.1" — additionally binds a human-principal claim (`on_behalf_of`).
# The version selects the canonicalization branch in
# `contracts.token.canonical_signing_bytes`; it is NOT itself part of a v1.0
# payload, because adding any key to that payload would change the signed bytes
# of every token already in flight.
TokenVersion = Literal["1.0", "1.1"]

# Whether the human was present when their agent acted. The co-work agent has to
# be able to say "you did this" versus "your agent did this while you were away".
SessionKind = Literal["autonomous", "cowork"]

# How far along an agent contract is toward general availability.
#   "sandbox"  — reachable only where a caller opts in explicitly; NOT part of
#                the autonomous fleet and never scheduled by the orchestrator.
#   "active"   — generally available (every pre-existing contract, by default).
#   "retired"  — kept for audit-trail resolution of historical tokens only.
# Defaulting to "active" keeps every existing contract byte-identical in meaning.
LifecycleStatus = Literal["sandbox", "active", "retired"]


class FailureMode(str, Enum):
    """What the agent does when it errors or is denied (agent_runtime.md §7)."""

    RETRY_THEN_ESCALATE = "retry_then_escalate"
    ESCALATE_IMMEDIATELY = "escalate_immediately"
    FAIL_CLOSED = "fail_closed"  # stop; emit nothing actionable
    FALLBACK_DEGRADED = "fallback_degraded"


class HumanInLoopTrigger(str, Enum):
    """Conditions that force human approval (agent_governance.md §9)."""

    SPEND_OVER_CEILING = "spend_over_ceiling"
    FIRST_EXTERNAL_LAUNCH = "first_external_launch"
    BRAND_LEGAL_SENSITIVE = "brand_legal_sensitive"
    AUTHORITY_EXCEEDED = "authority_exceeded"
    SECURITY_SEVERITY_HIGH = "security_severity_high"
    LOW_CONFIDENCE_IRREVERSIBLE = "low_confidence_irreversible"


class ToolGrant(BaseModel):
    """One entry in an agent's tool manifest (agent_governance.md §6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_id: str  # e.g. "llm.generate", "memory.search"
    purpose: str  # why this agent needs it (audited)
    max_calls_per_run: int | None = None
    requires_governance_token: bool = True


class AgentContract(BaseModel):
    """Static, auditable definition of one agent (agent_contract_registry.md §2).

    Stored in the registry, resolved by the Orchestrator, enforced by the tool
    proxy / Decision Engine.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str  # globally unique, lowercase_snake_case
    agent_role: str  # human-readable
    authority_level: AuthorityLevel
    department: str  # owning department channel

    # Maturity gate. Defaults to "active" so every pre-existing contract keeps
    # its current meaning; a "sandbox" contract is reachable only where a caller
    # names it explicitly and must never be picked up by the autonomous fleet.
    lifecycle_status: LifecycleStatus = "active"

    # I/O contracts — fully-qualified Pydantic model dotted paths
    input_schema: str
    output_schema: str

    # Capability declaration (the tool manifest)
    allowed_tools: list[ToolGrant]

    # Subset of `allowed_tools` tool_ids the agent may invoke through the LLM
    # tool-use loop (AgentExecutionService multi-turn path). Empty by default —
    # an agent with no `invocable_tools` runs the single-shot (prompt in,
    # text out) path unchanged, so adding this field is backward compatible
    # with every existing contract. Not every ToolGrant is meant to be
    # LLM-invocable (e.g. "orchestrator.delegate" is a workflow capability,
    # not something offered to the model as a callable tool).
    invocable_tools: list[str] = Field(default_factory=list)

    # Hard cap on tool-use loop iterations (agent_runtime.md). Exceeding this
    # is a governance escalation, not a silent truncation.
    max_tool_iterations: int = 5

    # Budgets — also become ceilings in the governance token
    max_token_budget: int
    max_execution_time_seconds: int

    # Escalation — ordered chain ending at a human role
    escalation_path: list[str]

    failure_mode: FailureMode

    # Memory scope — namespaces the agent may read / write
    memory_read_access: list[str]
    memory_write_access: list[str]

    # Governance
    governance_token_required: bool = True
    human_in_loop_triggers: list[HumanInLoopTrigger] = Field(default_factory=list)

    # Whether the MERE PRESENCE of a human-in-loop trigger is itself a
    # request-time verdict at the synchronous agent-execution gate
    # (decision_engine/evaluator.py stage 2.5).
    #
    # Defaults True, which is exactly the behaviour every contract had before
    # this field existed, so no existing contract's decision changes.
    #
    # Set False only when a contract's triggers name conditions that are
    # adjudicated somewhere that actually has the facts. Stage 2.5 runs BEFORE
    # the mint and before the model is called, against a proposal carrying no
    # spend, no scope and no security verdict, so for this vertical it can only
    # observe that a trigger is DECLARED -- never that one has occurred. The
    # conditions themselves are enforced by the ordered token pipeline
    # (contracts/token.py: SCOPE, BUDGET) and by the mint-time authority
    # intersection (app/principal/authority.py). See
    # docs/architecture/principal_dal_and_hitl_per_turn.md.
    #
    # This narrows WHEN a condition is adjudicated, never WHETHER it is.
    # `FIRST_EXTERNAL_LAUNCH` is unaffected and still defers unconditionally.
    defers_on_trigger_presence: bool = True

    @model_validator(mode="after")
    def _invocable_tools_subset_of_allowed(self) -> "AgentContract":
        allowed_ids = {grant.tool_id for grant in self.allowed_tools}
        unknown = set(self.invocable_tools) - allowed_ids
        if unknown:
            raise ValueError(
                f"invocable_tools {sorted(unknown)} not declared in allowed_tools"
                f" for agent_id={self.agent_id!r}"
            )
        return self


class OnBehalfOf(BaseModel):
    """The human-principal claim carried by a v1.1 governance token.

    Presence of this claim means: this token's authority derives from a HUMAN,
    and the tool proxy must additionally assert that the token's scope is still
    within that human's authority — see `app.principal.authority`.
    Absence means the classic autonomous shape (authority rooted at
    `human_owner`).

    It lives HERE, in `contracts`, rather than in `app.principal`, because it is
    part of the signed token wire format: `contracts` is an inner layer and must
    not import from `app`. `app.principal.models` re-exports it so the principal
    kernel keeps its own vocabulary.

    `authority_fingerprint` is the sha256 over the principal's compiled scope set
    at mint time (`app.principal.authority.fingerprint_scopes`). It is what lets a
    verifier detect that the human's authority changed after the token was minted,
    without a per-call permission join.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    principal_id: str
    authority_fingerprint: str
    session_kind: SessionKind


class GovernanceToken(BaseModel):
    """Signed proof of an agent's authority to act (agent_governance.md §4).

    Minted ONLY by the Governance Authority. Validated by the tool proxy and
    every integration adapter before any side-effecting action. Signed with
    ECDSA P-384 over the canonical serialization of all fields except
    `signature` (see token.py).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Which canonicalization the signature was computed over. Defaults to "1.0"
    # so a token deserialized from storage written before this field existed is
    # read exactly as it was signed.
    token_version: TokenVersion = "1.0"

    token_id: UUID  # unique; used for revocation
    agent_id: str
    authority_level: AuthorityLevel
    department: str

    # Ordered chain of agent_ids from root authority down to this agent.
    delegation_chain: list[str]

    # Concrete actions/tools authorized — subset of contract.allowed_tools.
    scope: list[str]

    # Budget ceilings enforced at the tool proxy / adapters
    max_token_budget: int
    max_execution_time_seconds: int

    # Validity window
    issued_at: datetime
    expires_at: datetime  # short-lived (minutes)

    nonce: str  # anti-replay
    signature: str  # ECDSA P-384 over canonical serialization, base64url

    # The human-principal claim. Present IFF token_version == "1.1" (enforced
    # below), so the version and the claim can never disagree on a token that
    # was constructed through validation.
    on_behalf_of: OnBehalfOf | None = None

    @model_validator(mode="after")
    def _version_and_claim_agree(self) -> "GovernanceToken":
        """A version is a promise about what the signature covers; keep it honest.

        The dangerous direction is a token whose bytes say "1.1" while carrying no
        principal claim: it would verify, yet bind nothing about the human. The
        bijection makes that unconstructible.

        NOTE: pydantic's `model_copy` does NOT re-run validators, so this cannot be
        the only line of defence — `contracts.token.canonical_signing_bytes`
        independently refuses to serialize a mismatched pair.
        """
        if self.token_version == "1.1" and self.on_behalf_of is None:
            raise ValueError(
                "token_version='1.1' requires an on_behalf_of claim; a v1.1 token "
                "without one would bind nothing about the human principal"
            )
        if self.token_version != "1.1" and self.on_behalf_of is not None:
            raise ValueError(
                f"on_behalf_of is only carried by v1.1 tokens, not "
                f"token_version={self.token_version!r}"
            )
        return self
