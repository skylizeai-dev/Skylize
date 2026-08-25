"""
LLM Gateway — SPECIFICATION (port + contracts).

This is the single, provider-abstracted egress to model providers. Agents never
name a provider; they request `llm.generate` through the tool proxy, which calls
this gateway after governance-token validation. The gateway:

  1. selects provider/model per tenant policy and cost routing;
  2. carries an OPTIONAL per-run token-budget ceiling checked before egress —
     see the gap note below: nothing populates it today, so this check is
     currently inert on every live path;
  3. records cost/quality in Langfuse keyed by `governance_token_id`;
  4. normalizes the provider response to a provider-neutral shape.

GAP — the request-level token budget is UNWIRED (documented, not fixed here).
`max_token_budget` / `tokens_used_so_far` on both request models default to
None, and no live construction site sets them: AgentExecutionService
(execution.py, both egresses), LLMStepRunner (orchestrator/runner.py), the
Temporal judge, and StructuredRequest all omit them. `_check_budget` in the
Anthropic adapter therefore returns immediately on its `is None` guard every
time, and `TokenBudgetExceeded` is never raised from that path.

What IS live is a DIFFERENT budget mechanism: the signed GovernanceToken's
`max_token_budget`, enforced by `contracts/token.py:validate_tool_call` (BUDGET
stage) which AgentExecutionService calls before each egress with the real
running total. That is what actually stops an over-budget run today, and it
raises the same `TokenBudgetExceeded` type — which is why the inert path looks
covered. Wiring the request-level fields is a separate, deliberate decision.

Foundation scope: the `LLMGateway` Protocol and the request/response/usage
models. Concrete provider adapters (Anthropic primary, OpenAI failover) are
implemented in Sprint 2 — they live behind this port so adding/removing a
provider never touches agent or domain code (anti-lock-in invariant).

CONTRACT NOTES for Sprint 2 implementers:
  - `model` is a logical name (e.g. "default", "fast", "reasoning"), NOT a
    provider model id. The gateway maps logical → concrete per tenant policy.
  - Token accounting, AS BUILT: the live pre-egress budget check is the
    GovernanceToken BUDGET stage (`contracts/token.py:validate_tool_call`),
    which AgentExecutionService runs before each call with the real running
    total. The `max_token_budget - tokens_used_so_far` check described on the
    request models below is a SECOND, currently unwired mechanism (see the GAP
    note above). The gateway records ACTUAL usage from the provider response.
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
    """Raised when a provider is unreachable: a retryable 5xx exhausted the
    bounded retry budget, or a connection failed outright — the latter raised
    IMMEDIATELY with no retry, because at that seam it is unknowable whether the
    provider received (and will bill) the request, and re-sending a possibly
    billed call risks double-spending. The gateway may fail over to another
    provider; if none is available the error propagates so the caller can
    degrade gracefully."""


class LLMRateLimited(Exception):
    """Raised when a provider keeps returning 429 after the bounded retry budget
    is exhausted (Retry-After honoured when present, else jittered exponential
    backoff). Distinct from LLMProviderUnavailable so callers can back off on
    rate limits separately from provider outages."""


class LLMTimeout(Exception):
    """Raised when a provider call exceeds the configured HTTP timeout
    (Settings.llm_timeout_seconds). NEVER retried (owner decision D2): a
    timed-out request may have COMPLETED and been billed by the provider while
    the response was lost, so retrying it could spend real money twice for at
    most one recorded ledger row. The SDK timeout exception is chained for
    diagnosis; neither message carries key material."""


class LLMMalformedResponse(Exception):
    """Raised when the provider returns a 2xx whose body cannot be parsed as a
    Message. A parse failure is a PROVIDER failure, and it is NOT retried
    (owner decision D4): a served-but-unparseable response means the provider
    completed — and billed — the generation, so a retry would double-spend
    exactly like a retried timeout. The parser error is chained for diagnosis;
    neither message carries key material."""


class LLMAuthenticationError(Exception):
    """Raised on a 401 from the provider — fail closed immediately, no retry.

    The message is static and carries NO key material; the originating SDK
    exception is deliberately not chained, so no credential can leak into the
    exception string, a log record, or an emitted event via this path."""


class LLMModelNotPriced(Exception):
    """Raised when a concrete provider model id has no configured price entry.

    Cost estimation keys on the EXACT concrete model id (not a substring), so an
    unrecognized model id fails loudly instead of being mispriced as a default
    tier — a wrong bill is worse than a loud error."""


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

    # Attribution context — REQUIRED and threaded (never resolved by a DB
    # lookup in the adapter): `correlation_id` is the run-level id minted by
    # the caller's entrypoint (e.g. AgentExecutionService's run_id or the
    # Orchestrator's correlation_id) and `agent_id` is the acting agent
    # (GovernanceToken.agent_id on token-bearing paths). Required fields turn
    # a missed construction site into a type error, not a silent null.
    correlation_id: UUID
    agent_id: str

    # Optional run-budget context. When set, adapters refuse a call whose
    # requested_max_tokens exceeds (max_token_budget - tokens_used_so_far)
    # BEFORE any provider egress → TokenBudgetExceeded.
    # UNWIRED TODAY: no live construction site sets either field, so the
    # adapter's `_check_budget` short-circuits on its `is None` guard every
    # time. The budget actually enforced pre-egress is the GovernanceToken's
    # (contracts/token.py BUDGET stage). See the GAP note in the module
    # docstring. Left in place — wiring them is a separate decision, not dead
    # code to delete.
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

    # Attribution context — same contract as LLMGenerateRequest.
    correlation_id: UUID
    agent_id: str

    # Optional run-budget context. When set, adapters refuse a call whose
    # requested_max_tokens exceeds (max_token_budget - tokens_used_so_far)
    # BEFORE any provider egress → TokenBudgetExceeded.
    # UNWIRED TODAY: no live construction site sets either field, so the
    # adapter's `_check_budget` short-circuits on its `is None` guard every
    # time. The budget actually enforced pre-egress is the GovernanceToken's
    # (contracts/token.py BUDGET stage). See the GAP note in the module
    # docstring. Left in place — wiring them is a separate decision, not dead
    # code to delete.
    max_token_budget: int | None = None
    tokens_used_so_far: int | None = None


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
