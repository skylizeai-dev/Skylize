"""
LLMAgentRunner unit tests — the governed agent lifecycle.

Drives the runner with in-test fakes for the registry, Governance Authority,
tool proxy, and audit sink so each lifecycle stage
(RESOLVE→GATE→VALIDATE→MINT→RUN→EMIT→AUDIT) is exercised in isolation.

Covers:
  - happy path → AgentRunResult returned, success audited
  - unknown agent_id → ContractNotFound, governance never touched
  - gate blocked → GovernanceGateBlocked, no mint / no dispatch
  - mint raises → TokenMintFailed, no dispatch
  - dispatch times out → RunTimeout, timeout audited
  - requested > contract budget → effective_budget clamped to the contract
  - audit failure never propagates (success and failure paths)
  - every terminal state emits exactly one audit event
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest

from skylize.adapters.llm.gateway import LLMGenerateResponse, LLMUsage
from skylize.contracts.base import (
    AgentContract,
    FailureMode,
    GovernanceToken,
    ToolGrant,
)
from skylize.runtime import (
    AgentRunInput,
    AgentRunResult,
    AgentRunnerError,
    ContractNotFound,
    GovernanceGateBlocked,
    LLMAgentRunner,
    RunTimeout,
    TokenMintFailed,
)

ORG = "org_test"
AGENT = "hook_generator_agent"


# ---------------------------------------------------------------------------
# Builders + fakes
# ---------------------------------------------------------------------------

def _contract(*, budget: int = 8_000, ttl: int = 60, tools: tuple[str, ...] = ("llm.generate",)) -> AgentContract:
    return AgentContract(
        agent_id=AGENT,
        agent_role="Hook Generator",
        authority_level="worker",
        department="creative",
        # Real importable Pydantic models so VALIDATE's resolve_model succeeds.
        input_schema="skylize.runtime.agent_runner.AgentRunInput",
        output_schema="skylize.runtime.agent_runner.AgentRunResult",
        allowed_tools=[ToolGrant(tool_id=t, purpose="test") for t in tools],
        max_token_budget=budget,
        max_execution_time_seconds=ttl,
        escalation_path=["copy_director", "human_owner"],
        failure_mode=FailureMode.FALLBACK_DEGRADED,
        memory_read_access=[],
        memory_write_access=[],
    )


def _token(*, budget: int = 8_000, ttl: int = 60) -> GovernanceToken:
    now = datetime.now(timezone.utc)
    return GovernanceToken(
        token_id=uuid4(),
        agent_id=AGENT,
        authority_level="worker",
        department="creative",
        delegation_chain=[AGENT],
        scope=["llm.generate"],
        max_token_budget=budget,
        max_execution_time_seconds=ttl,
        issued_at=now,
        expires_at=now + timedelta(seconds=300),
        nonce=uuid4().hex,
        signature="test-signature",
    )


def _response() -> LLMGenerateResponse:
    return LLMGenerateResponse(
        text="generated",
        provider="fake",
        concrete_model="fake-1",
        usage=LLMUsage(prompt_tokens=5, completion_tokens=7, total_tokens=12),
        cost_usd_micros=42,
    )


class FakeRegistry:
    def __init__(self, contract: AgentContract | None) -> None:
        self._contract = contract
        self.calls: list[str] = []

    async def get_contract(self, agent_id: str) -> AgentContract | None:
        self.calls.append(agent_id)
        return self._contract


class FakeGovernance:
    def __init__(
        self,
        *,
        blocked: bool = False,
        reason: str = "",
        token: GovernanceToken | None = None,
        mint_error: Exception | None = None,
    ) -> None:
        self._blocked = blocked
        self._reason = reason
        self._token = token if token is not None else _token()
        self._mint_error = mint_error
        self.check_calls: list[tuple[str, str]] = []
        self.mint_calls: list[dict[str, Any]] = []

    async def check_live_state(self, agent_id: str, org_id: str) -> tuple[bool, str]:
        self.check_calls.append((agent_id, org_id))
        return (self._blocked, self._reason)

    async def mint_token(
        self,
        *,
        agent_id: str,
        org_id: str,
        scope: list[str],
        max_token_budget: int,
        max_execution_time_seconds: int,
    ) -> GovernanceToken:
        self.mint_calls.append(
            {
                "agent_id": agent_id,
                "org_id": org_id,
                "scope": scope,
                "max_token_budget": max_token_budget,
                "max_execution_time_seconds": max_execution_time_seconds,
            }
        )
        if self._mint_error is not None:
            raise self._mint_error
        return self._token


class FakeToolProxy:
    def __init__(self, *, response: LLMGenerateResponse | None = None, hang: bool = False) -> None:
        self._response = response if response is not None else _response()
        self._hang = hang
        self.dispatch_calls: list[dict[str, Any]] = []

    async def dispatch_llm(self, token, tool_call, allowed_tools, gateway):
        self.dispatch_calls.append(
            {"token": token, "tool_call": tool_call, "allowed_tools": allowed_tools}
        )
        if self._hang:
            await asyncio.sleep(30)  # never completes within the contract ceiling
        return self._response


class FakeAudit:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.events: list[dict[str, Any]] = []

    async def __call__(self, event: dict[str, Any]) -> None:
        if self._fail:
            raise RuntimeError("audit sink is down")
        self.events.append(event)


def _runner(registry, governance, proxy, audit) -> LLMAgentRunner:
    return LLMAgentRunner(
        registry=registry,
        governance=governance,
        tool_proxy=proxy,
        gateway=object(),  # never touched directly; only handed to the proxy
        audit_publisher=audit,
    )


def _input(*, requested_max_tokens: int = 1_000) -> AgentRunInput:
    return AgentRunInput(
        agent_id=AGENT,
        org_id=ORG,
        prompt="Write three hooks.",
        system="You are a copywriter.",
        requested_max_tokens=requested_max_tokens,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_happy_path_returns_result_and_audits_success() -> None:
    governance = FakeGovernance(token=_token())
    proxy = FakeToolProxy(response=_response())
    audit = FakeAudit()
    runner = _runner(FakeRegistry(_contract()), governance, proxy, audit)

    result = await runner.run(_input())

    assert isinstance(result, AgentRunResult)
    assert result.text == "generated"
    assert result.concrete_model == "fake-1"
    assert result.cost_usd_micros == 42
    assert result.usage.total_tokens == 12
    assert result.governance_token_id == governance._token.token_id
    # Exactly one terminal audit, and it is the success record.
    assert len(audit.events) == 1
    assert audit.events[0]["stage"] == "RUN_SUCCESS"
    # Dispatch carried the narrowed scope and the run's token.
    assert proxy.dispatch_calls[0]["tool_call"].tool_id == "llm.generate"
    assert proxy.dispatch_calls[0]["allowed_tools"] == ["llm.generate"]


async def test_unknown_agent_raises_contract_not_found_without_governance() -> None:
    governance = FakeGovernance()
    proxy = FakeToolProxy()
    audit = FakeAudit()
    runner = _runner(FakeRegistry(None), governance, proxy, audit)

    with pytest.raises(ContractNotFound) as exc:
        await runner.run(_input())

    assert exc.value.agent_id == AGENT
    assert governance.check_calls == []  # gate never consulted
    assert governance.mint_calls == []
    assert proxy.dispatch_calls == []
    assert len(audit.events) == 1
    assert audit.events[0]["stage"] == "RESOLVE_FAILED"


async def test_gate_blocked_raises_without_mint_or_dispatch() -> None:
    governance = FakeGovernance(blocked=True, reason="agent suspended")
    proxy = FakeToolProxy()
    audit = FakeAudit()
    runner = _runner(FakeRegistry(_contract()), governance, proxy, audit)

    with pytest.raises(GovernanceGateBlocked) as exc:
        await runner.run(_input())

    assert exc.value.reason == "agent suspended"
    assert governance.mint_calls == []  # never minted for a blocked agent
    assert proxy.dispatch_calls == []
    assert len(audit.events) == 1
    assert audit.events[0]["stage"] == "GATE_BLOCKED"


async def test_mint_failure_raises_token_mint_failed_without_dispatch() -> None:
    boom = RuntimeError("HSM unavailable")
    governance = FakeGovernance(mint_error=boom)
    proxy = FakeToolProxy()
    audit = FakeAudit()
    runner = _runner(FakeRegistry(_contract()), governance, proxy, audit)

    with pytest.raises(TokenMintFailed) as exc:
        await runner.run(_input())

    assert exc.value.cause is boom
    assert proxy.dispatch_calls == []  # no egress without a token
    assert len(audit.events) == 1
    assert audit.events[0]["stage"] == "MINT_FAILED"


async def test_dispatch_timeout_raises_run_timeout_and_audits() -> None:
    governance = FakeGovernance(token=_token(ttl=1))
    proxy = FakeToolProxy(hang=True)
    audit = FakeAudit()
    # ttl=1 keeps the wall-clock ceiling short so the test is fast.
    runner = _runner(FakeRegistry(_contract(ttl=1)), governance, proxy, audit)

    with pytest.raises(RunTimeout) as exc:
        await runner.run(_input())

    assert exc.value.agent_id == AGENT
    assert exc.value.timeout_seconds == 1
    assert len(audit.events) == 1
    assert audit.events[0]["stage"] == "RUN_TIMEOUT"


async def test_unresolvable_input_schema_fails_closed_without_mint() -> None:
    governance = FakeGovernance(token=_token())
    proxy = FakeToolProxy()
    audit = FakeAudit()
    bad = _contract()
    object.__setattr__(bad, "input_schema", "skylize.does.not.Exist")  # frozen model
    runner = _runner(FakeRegistry(bad), governance, proxy, audit)

    with pytest.raises(AgentRunnerError):
        await runner.run(_input())

    assert governance.mint_calls == []  # malformed contract never mints
    assert proxy.dispatch_calls == []
    assert len(audit.events) == 1
    assert audit.events[0]["stage"] == "VALIDATE_FAILED"


async def test_requested_budget_is_clamped_to_contract_ceiling() -> None:
    governance = FakeGovernance(token=_token())
    proxy = FakeToolProxy()
    audit = FakeAudit()
    runner = _runner(FakeRegistry(_contract(budget=8_000)), governance, proxy, audit)

    # Request far above the contract ceiling.
    await runner.run(_input(requested_max_tokens=50_000))

    # effective_budget == contract.max_token_budget, applied to BOTH the mint
    # and the dispatched tool call.
    assert governance.mint_calls[0]["max_token_budget"] == 8_000
    assert proxy.dispatch_calls[0]["tool_call"].params["requested_max_tokens"] == 8_000


async def test_audit_failure_does_not_propagate_on_success() -> None:
    governance = FakeGovernance(token=_token())
    proxy = FakeToolProxy(response=_response())
    runner = _runner(FakeRegistry(_contract()), governance, proxy, FakeAudit(fail=True))

    # A broken audit sink must not turn a successful run into a failure.
    result = await runner.run(_input())
    assert result.text == "generated"


async def test_audit_failure_does_not_propagate_on_failure_path() -> None:
    runner = _runner(FakeRegistry(None), FakeGovernance(), FakeToolProxy(), FakeAudit(fail=True))

    # The real failure (ContractNotFound) surfaces; the audit error is swallowed.
    with pytest.raises(ContractNotFound):
        await runner.run(_input())


@pytest.mark.parametrize(
    ("make_runner", "expected_exc", "expected_stage"),
    [
        (
            lambda audit: _runner(FakeRegistry(None), FakeGovernance(), FakeToolProxy(), audit),
            ContractNotFound,
            "RESOLVE_FAILED",
        ),
        (
            lambda audit: _runner(
                FakeRegistry(_contract()),
                FakeGovernance(blocked=True, reason="killed"),
                FakeToolProxy(),
                audit,
            ),
            GovernanceGateBlocked,
            "GATE_BLOCKED",
        ),
        (
            lambda audit: _runner(
                FakeRegistry(_contract()),
                FakeGovernance(mint_error=RuntimeError("nope")),
                FakeToolProxy(),
                audit,
            ),
            TokenMintFailed,
            "MINT_FAILED",
        ),
        (
            lambda audit: _runner(
                FakeRegistry(_contract(ttl=1)),
                FakeGovernance(token=_token(ttl=1)),
                FakeToolProxy(hang=True),
                audit,
            ),
            RunTimeout,
            "RUN_TIMEOUT",
        ),
    ],
)
async def test_every_terminal_state_emits_exactly_one_audit(
    make_runner, expected_exc, expected_stage
) -> None:
    audit = FakeAudit()
    runner = make_runner(audit)

    with pytest.raises(expected_exc):
        await runner.run(_input())

    assert len(audit.events) == 1
    assert audit.events[0]["stage"] == expected_stage


async def test_success_path_emits_exactly_one_audit() -> None:
    audit = FakeAudit()
    runner = _runner(FakeRegistry(_contract()), FakeGovernance(token=_token()), FakeToolProxy(), audit)

    await runner.run(_input())

    assert len(audit.events) == 1
    assert audit.events[0]["stage"] == "RUN_SUCCESS"
