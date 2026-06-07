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

from pydantic import BaseModel, ConfigDict, Field

# Canonical authority levels — IDENTICAL to agent_governance.md §2.
AuthorityLevel = Literal["executive", "vp", "director", "manager", "worker"]


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


class GovernanceToken(BaseModel):
    """Signed proof of an agent's authority to act (agent_governance.md §4).

    Minted ONLY by the Governance Authority. Validated by the tool proxy and
    every integration adapter before any side-effecting action. Signed with
    ECDSA P-384 over the canonical serialization of all fields except
    `signature` (see token.py).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

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
