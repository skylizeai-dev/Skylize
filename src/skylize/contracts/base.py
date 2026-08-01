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
