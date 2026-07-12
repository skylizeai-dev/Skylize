"""ToolDefinition — the agent-facing equivalent of AgentContract.

A tool is a typed, versioned capability an agent may invoke through the tool
proxy (`IF-TOOL`). `description` is sent to the LLM verbatim as the tool-use
description, so it must precisely state when the tool applies.

`tool_id` doubles as the governance scope key: `GovernanceAuthority.mint`
defaults a token's `scope` to the agent contract's `ToolGrant.tool_id` list,
and `contracts.token.validate_tool_call` checks membership by that same
string. There is no separate scope namespace in this codebase — keep tool_ids
and ToolGrant.tool_ids identical.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

ToolCategory = Literal["memory", "search", "integration", "compute"]


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Per-call context handed to a tool's handler alongside its validated input."""

    org_id: str
    agent_id: str
    correlation_id: UUID


ToolHandler = Callable[[BaseModel, ToolContext], Awaitable[BaseModel]]


class ToolDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    tool_id: str
    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    category: ToolCategory
    handler: ToolHandler


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_id: str
    output: BaseModel
    call_id: str | None = None

    def output_json(self) -> dict[str, Any]:
        return self.output.model_dump(mode="json")


class ToolError(Exception):
    """Base of the tool-invocation error hierarchy."""


class ToolNotRegistered(ToolError):
    """Unknown tool_id — the registry fails closed (IF-TOOL 404 equivalent)."""


class ToolPermissionDenied(ToolError):
    """Governance token failed `validate_tool_call` (IF-TOOL 403 equivalent)."""

    def __init__(self, reason: str, *, failed_stage: str | None = None) -> None:
        super().__init__(reason)
        self.failed_stage = failed_stage


class ToolConvergenceDenied(ToolPermissionDenied):
    """The convergence breaker tripped: this agent repeated the same tool call
    (same input) back-to-back within the workflow, so the runaway loop was
    suspended by the Governance Authority (agent_governance.md §7).

    Subclasses ToolPermissionDenied so it routes through the same denied-call
    audit/handling path the scope/budget/revocation denials already use; the
    Authority has already emitted the convergence_failure + breaker events.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason, failed_stage="convergence")


class ToolCallLimitExceeded(ToolPermissionDenied):
    """The tool was invoked more than its `ToolGrant.max_calls_per_run` times
    within this workflow (agent_governance.md §6). This is a proxy-side ceiling
    derived from the contract, not one of `contracts.token.ValidationStage`'s
    token-validation stages (signature/expiry/revocation/scope/budget/
    delegation), so — mirroring `ToolConvergenceDenied` — it gets its own
    `failed_stage` rather than being shoehorned into `scope` or `budget`.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason, failed_stage="call_limit")


class ToolInputError(ToolError):
    """`input_data` failed validation against the tool's `input_schema`."""


class ToolExecutionError(ToolError):
    """The tool's handler raised while executing."""
