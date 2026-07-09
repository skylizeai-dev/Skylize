"""
The governed LLM agent runner — the runtime realization of the agent lifecycle.

This is the single primitive that takes one agent run from a raw prompt to a
governed, audited model response by walking the canonical lifecycle from
``03_agent_runtime.md`` §6:

    RESOLVE  → registry returns the AgentContract (fail closed if unknown)
    GATE     → governance live-state: not suspended / circuit-broken / killed
    VALIDATE → the contract's input_schema is resolvable (well-formed contract)
    MINT     → a run-scoped GovernanceToken, scope narrowed to ["llm.generate"]
    RUN      → dispatch through the tool proxy under a wall-clock ceiling
    EMIT     → normalize the provider response into an AgentRunResult
    AUDIT    → every terminal state emits exactly one audit event

Fail-closed is the rule at every step: an unknown agent, a blocked agent, a
mint failure, or a timeout aborts the run with a typed error and an audit
record. Audit emission never propagates — a broken audit sink must not turn a
successful run into a failure (or mask the real failure of a failing one).

The registry and the Governance Authority are injected as Protocols, never as
concrete classes, so this module stays decoupled from their implementations
(the import-linter boundary and the framework-swap invariant in §10).
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
import structlog

from ..adapters.llm.gateway import LLMGateway, LLMUsage
from ..config import Settings
from ..contracts.base import AgentContract, GovernanceToken
from ..contracts.registry import ContractSchemaError, resolve_model
from .tool_proxy import ToolCallRequest, ToolProxy

__all__ = [
    "AgentRunInput",
    "AgentRunResult",
    "AgentRunnerError",
    "ContractNotFound",
    "GovernanceGateBlocked",
    "TokenMintFailed",
    "RunTimeout",
    "AgentRegistryProtocol",
    "GovernanceAuthorityProtocol",
    "LLMAgentRunner",
]

log = structlog.get_logger(__name__)

# The only tool an LLM run is ever granted. The minted scope is never wider
# than this, and the proxy independently re-checks it against the contract.
_LLM_TOOL_ID = "llm.generate"


# ---------------------------------------------------------------------------
# I/O models
# ---------------------------------------------------------------------------

class AgentRunInput(BaseModel):
    """One governed LLM run request, addressed to an agent by id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str
    org_id: str
    prompt: str
    system: str | None = None
    model: str = "default"  # logical model name; the gateway maps it concrete
    requested_max_tokens: int = Field(gt=0, le=200_000)


class AgentRunResult(BaseModel):
    """The normalized, provider-neutral outcome of a successful run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    usage: LLMUsage
    cost_usd_micros: int
    concrete_model: str
    governance_token_id: UUID
    run_id: UUID


# ---------------------------------------------------------------------------
# Typed failure hierarchy
# ---------------------------------------------------------------------------

class AgentRunnerError(Exception):
    """Base for every failure surfaced by ``LLMAgentRunner.run``."""


class ContractNotFound(AgentRunnerError):
    """RESOLVE failed: the agent_id is not registered. No fallback — fail closed."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(f"agent_id={agent_id!r} is not registered; unknown agents fail closed")
        self.agent_id = agent_id


class GovernanceGateBlocked(AgentRunnerError):
    """GATE refused the run: the agent is suspended, circuit-broken, or killed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TokenMintFailed(AgentRunnerError):
    """MINT failed: the Governance Authority could not issue a token."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(f"governance token mint failed: {cause}")
        self.cause = cause


class RunTimeout(AgentRunnerError):
    """RUN exceeded the contract's ``max_execution_time_seconds`` wall clock."""

    def __init__(self, agent_id: str, timeout_seconds: int) -> None:
        super().__init__(f"run for agent {agent_id!r} exceeded {timeout_seconds}s")
        self.agent_id = agent_id
        self.timeout_seconds = timeout_seconds


# ---------------------------------------------------------------------------
# Injected collaborators (Protocols — no concrete coupling)
# ---------------------------------------------------------------------------

@runtime_checkable
class AgentRegistryProtocol(Protocol):
    """Resolves an agent_id to its contract, or ``None`` when unknown."""

    async def get_contract(self, agent_id: str) -> AgentContract | None: ...


@runtime_checkable
class GovernanceAuthorityProtocol(Protocol):
    """The seam onto the Governance Authority used by the runner.

    ``check_live_state`` returns ``(is_blocked, reason)`` — the live gate that
    must be consulted BEFORE a token is minted. ``mint_token`` issues a
    run-scoped token whose ceilings are already narrowed to this run.
    """

    async def check_live_state(self, agent_id: str, org_id: str) -> tuple[bool, str]: ...

    async def mint_token(
        self,
        *,
        agent_id: str,
        org_id: str,
        scope: list[str],
        max_token_budget: int,
        max_execution_time_seconds: int,
    ) -> GovernanceToken: ...


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

class LLMAgentRunner:
    """Drives one agent run through RESOLVE→GATE→VALIDATE→MINT→RUN→EMIT→AUDIT.

    It never calls the LLM gateway directly: ``llm.generate`` is dispatched
    through the tool proxy so the governance token is validated and the run
    budget enforced before any provider egress.
    """

    def __init__(
        self,
        *,
        registry: AgentRegistryProtocol,
        governance: GovernanceAuthorityProtocol,
        tool_proxy: ToolProxy,
        gateway: LLMGateway,
        audit_publisher: object,  # async callable (event: dict) -> None
        settings: Settings | None = None,
    ) -> None:
        self._registry = registry
        self._governance = governance
        self._tool_proxy = tool_proxy
        self._gateway = gateway
        self._audit_publisher = audit_publisher
        self._settings = settings

    async def run(self, run_input: AgentRunInput) -> AgentRunResult:
        run_id = uuid4()

        # RESOLVE — fail closed on an unknown agent; never fall back.
        contract = await self._registry.get_contract(run_input.agent_id)
        if contract is None:
            await self._audit(run_id, run_input, "RESOLVE_FAILED", "contract not found")
            raise ContractNotFound(run_input.agent_id)

        # GATE — live governance state, consulted BEFORE minting. A suspended,
        # circuit-broken, or killed agent must never receive a token.
        is_blocked, reason = await self._governance.check_live_state(
            run_input.agent_id, run_input.org_id
        )
        if is_blocked:
            await self._audit(run_id, run_input, "GATE_BLOCKED", reason)
            raise GovernanceGateBlocked(reason)

        # VALIDATE — the contract's declared input_schema must resolve to an
        # importable Pydantic model. A malformed contract fails closed here,
        # before any token is minted or any side effect is possible.
        try:
            resolve_model(contract.input_schema)
        except ContractSchemaError as exc:
            await self._audit(run_id, run_input, "VALIDATE_FAILED", str(exc))
            raise AgentRunnerError(
                f"input_schema unresolvable for {run_input.agent_id!r}: {exc}"
            ) from exc

        # MINT — scope narrowed to llm.generate; budget never wider than the
        # contract ceiling (effective_budget = min(requested, contract)).
        effective_budget = min(run_input.requested_max_tokens, contract.max_token_budget)
        try:
            token = await self._governance.mint_token(
                agent_id=run_input.agent_id,
                org_id=run_input.org_id,
                scope=[_LLM_TOOL_ID],
                max_token_budget=effective_budget,
                max_execution_time_seconds=contract.max_execution_time_seconds,
            )
        except Exception as exc:  # any mint failure is terminal and audited
            await self._audit(run_id, run_input, "MINT_FAILED", str(exc))
            raise TokenMintFailed(exc) from exc

        # RUN — dispatch through the proxy under the contract's wall-clock cap.
        tool_call = ToolCallRequest(
            tool_id=_LLM_TOOL_ID,
            governance_token_id=token.token_id,
            org_id=run_input.org_id,
            params={
                "prompt": run_input.prompt,
                "system": run_input.system,
                "model": run_input.model,
                "requested_max_tokens": effective_budget,
            },
        )
        allowed_tools = [grant.tool_id for grant in contract.allowed_tools]
        try:
            response = await asyncio.wait_for(
                self._tool_proxy.dispatch_llm(
                    token, tool_call, allowed_tools, self._gateway
                ),
                timeout=contract.max_execution_time_seconds,
            )
        except asyncio.TimeoutError:
            await self._audit(
                run_id, run_input, "RUN_TIMEOUT",
                f"exceeded {contract.max_execution_time_seconds}s",
            )
            raise RunTimeout(run_input.agent_id, contract.max_execution_time_seconds)

        # EMIT — normalize into the provider-neutral result and audit success.
        result = AgentRunResult(
            text=response.text,
            usage=response.usage,
            cost_usd_micros=response.cost_usd_micros,
            concrete_model=response.concrete_model,
            governance_token_id=token.token_id,
            run_id=run_id,
        )
        await self._audit(run_id, run_input, "RUN_SUCCESS", "completed")
        return result

    # ------------------------------------------------------------------
    # Audit — emit one event per terminal state; never let it propagate.
    # ------------------------------------------------------------------

    async def _audit(
        self, run_id: UUID, run_input: AgentRunInput, stage: str, detail: str
    ) -> None:
        try:
            event = {
                "type": "agent.run_audited",
                "run_id": str(run_id),
                "agent_id": run_input.agent_id,
                "org_id": run_input.org_id,
                "stage": stage,
                "detail": detail,
            }
            await self._audit_publisher(event)  # type: ignore[operator]
        except Exception:
            log.warning("agent_runner.audit_publish_failed", stage=stage, run_id=str(run_id))
