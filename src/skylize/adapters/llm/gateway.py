"""
LLM Gateway — SPECIFICATION (port + contracts).

This is the single, provider-abstracted egress to model providers. Agents never
name a provider; they request `llm.generate` through the tool proxy, which calls
this gateway after governance-token validation. The gateway:

  1. selects provider/model per tenant policy and cost routing;
  2. enforces the per-run **token budget ceiling** BEFORE egress
     (a call that would exceed `max_token_budget` is refused → TokenBudgetExceeded);
  3. records cost/quality in Langfuse keyed by `governance_token_id`;
  4. normalizes the provider response to a provider-neutral shape.

Foundation scope: the `LLMGateway` Protocol and the request/response/usage
models. Concrete provider adapters (Anthropic primary, OpenAI failover) are
implemented in Sprint 2 — they live behind this port so adding/removing a
provider never touches agent or domain code (anti-lock-in invariant).

CONTRACT NOTES for Sprint 2 implementers:
  - `model` is a logical name (e.g. "default", "fast", "reasoning"), NOT a
    provider model id. The gateway maps logical → concrete per tenant policy.
  - Token accounting: `requested_max_tokens` is checked against the remaining
    run budget (`max_token_budget - tokens_used_so_far`) by the tool proxy
    before this gateway is called; the gateway additionally records ACTUAL usage
    from the provider response and the proxy debits the run ledger by
    `usage.total_tokens`.
  - Streaming is out of scope for MVP; `generate` returns a complete response.
  - The gateway is the ONLY module permitted to import a provider SDK.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from ...tools.base import ToolDefinition


class LLMContentBlock(BaseModel):
    """One provider-neutral content block in a multi-turn message.

    `kind="text"` carries `text`; `kind="tool_use"` carries the tool call
    (`tool_use_id` / `tool_name` / `tool_input`); `kind="tool_result"` carries the
    tool's output back to the model (`tool_use_id` / `tool_output`, with
    `is_error` set on failure). Adapters translate this to/from provider wire
    formats so agent/runtime code never sees a provider-specific block shape.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["text", "tool_use", "tool_result"]
    text: str | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: str | None = None
    is_error: bool = False


class LLMMessage(BaseModel):
    """One turn in a multi-turn exchange: a role plus its content blocks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: str  # "user" | "assistant"
    content: list[LLMContentBlock]


class TokenBudgetExceeded(Exception):
    """Raised when a generation would exceed the run's token budget ceiling."""


class LLMProviderUnavailable(Exception):
    """Raised when a provider is unreachable after retries (retryable 5xx/network
    errors exhausted). The gateway may fail over to another provider; if none is
    available the error propagates so the caller can degrade gracefully."""


class LLMGenerateRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Provider-neutral logical model name; gateway maps to a concrete model.
    model: str = "default"
    prompt: str
    system: str | None = None
    requested_max_tokens: int = Field(gt=0)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)

    # Governance / tenancy context — required for cost attribution & isolation.
    governance_token_id: UUID
    org_id: str

    # Optional run-budget context. When set, adapters refuse a call whose
    # requested_max_tokens exceeds (max_token_budget - tokens_used_so_far)
    # BEFORE any provider egress → TokenBudgetExceeded.
    max_token_budget: int | None = None
    tokens_used_so_far: int | None = None


class LLMGenerateWithToolsRequest(BaseModel):
    """Multi-turn, tool-enabled generation request.

    Unlike `LLMGenerateRequest` (single prompt), this carries a full message
    history (`messages`) so the model can be driven through a tool-use loop:
    assistant emits `tool_use` blocks, the runtime executes them and appends
    `tool_result` blocks, and the request is re-issued until the model stops.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = "default"
    system: str | None = None
    messages: list[LLMMessage]
    requested_max_tokens: int = Field(gt=0)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)

    governance_token_id: UUID
    org_id: str


class LLMUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class LLMGenerateResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    provider: str  # which provider actually served (audit/observability)
    concrete_model: str  # the provider-side model id used
    usage: LLMUsage
    cost_usd_micros: int = 0  # cost in millionths of a USD; 0 if not priced

    # Multi-turn / tool-use path (empty on single-shot `generate`): why the model
    # stopped ("end_turn" | "tool_use" | ...) and the structured content blocks
    # (assistant text + any `tool_use` calls the runtime must execute).
    stop_reason: str | None = None
    content: list[LLMContentBlock] = Field(default_factory=list)


@runtime_checkable
class LLMGateway(Protocol):
    """The provider-abstracted LLM port.

    Implemented in Sprint 2 by `AnthropicAdapter` (primary). The async method is
    canonical; `generate_sync` exists for the thread-pool path used when a
    LangGraph node must call the gateway from synchronous code.
    """

    async def generate(self, request: LLMGenerateRequest) -> LLMGenerateResponse:
        """Route, enforce budget, call provider, record cost, normalize response.

        Raises TokenBudgetExceeded before egress if the request cannot fit the
        run's remaining token budget.
        """
        ...

    def generate_sync(self, request: LLMGenerateRequest) -> LLMGenerateResponse:
        """Blocking variant for execution inside a thread-pool executor."""
        ...

    async def generate_with_tools(
        self, request: LLMGenerateWithToolsRequest, tools: list[ToolDefinition]
    ) -> LLMGenerateResponse:
        """Multi-turn tool-enabled generation through the governed tool set."""
        ...
