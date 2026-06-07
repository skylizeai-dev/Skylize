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

from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TokenBudgetExceeded(Exception):
    """Raised when a generation would exceed the run's token budget ceiling."""


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


@runtime_checkable
class LLMGateway(Protocol):
    """The provider-abstracted LLM port.

    Implemented in Sprint 2 by `AnthropicAdapter` (primary). The async method is
    canonical; `generate_sync` exists for the thread-pool path used when CrewAI
    runs inside a LangGraph node.
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
