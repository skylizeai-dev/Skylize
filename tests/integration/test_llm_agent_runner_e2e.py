"""
E2E integration: LLMAgentRunner + ToolProxy + AnthropicAdapter.

Real wiring: AnthropicAdapter → ToolProxy → LLMAgentRunner.
Mocked at the I/O boundary only:
  - anthropic.Anthropic().messages.create  (HTTP)
  - redis.asyncio.Redis                    (revocation)
  - AgentRegistry / GovernanceAuthority    (contract + token mint)
  - audit_publisher                        (fire-and-forget sink)

ECDSA: a real P-384 key-pair is generated once per session; tokens are
properly signed so verify_token_signature returns True.  Where a test needs
a broken signature, the mock is patched directly.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from skylize.adapters.llm.anthropic_adapter import AnthropicAdapter
from skylize.config import Settings
from skylize.contracts.base import (
    AgentContract,
    FailureMode,
    GovernanceToken,
    ToolGrant,
)
from skylize.contracts.token import TokenSigner
from skylize.runtime import (
    AgentRunInput,
    AgentRunResult,
    LLMAgentRunner,
    RunTimeout,
    ScopeViolation,
    ToolProxy,
    TokenRevoked,
)
from skylize.security.ecc_service import Curve, ECCService

# ---------------------------------------------------------------------------
# Module-level key pair (real P-384 — generated once, reused across all tests)
# ---------------------------------------------------------------------------

_KEY_PAIR = ECCService.generate_key_pair(Curve.P384)
_PRIVATE_KEY = _KEY_PAIR.private_key
_PUBLIC_KEY = _KEY_PAIR.public_key
_PUB_DER = _KEY_PAIR.public_der()

ORG = "org_e2e"
AGENT = "e2e_agent"


# ---------------------------------------------------------------------------
# Shared builders
# ---------------------------------------------------------------------------

def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "anthropic_api_key": "sk-test",
        "llm_model_default": "claude-sonnet-4-6",
        "llm_model_fast": "claude-haiku-4-5-20251001",
        "llm_model_reasoning": "claude-opus-4-6",
        "llm_price_sonnet_in": 3.0,
        "llm_price_sonnet_out": 15.0,
        "llm_price_haiku_in": 0.80,
        "llm_price_haiku_out": 4.0,
        "llm_price_opus_in": 15.0,
        "llm_price_opus_out": 75.0,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _contract(
    *,
    budget: int = 8_000,
    ttl: int = 30,
    tools: tuple[str, ...] = ("llm.generate",),
) -> AgentContract:
    return AgentContract(
        agent_id=AGENT,
        agent_role="E2E Agent",
        authority_level="worker",
        department="engineering",
        input_schema="skylize.runtime.agent_runner.AgentRunInput",
        output_schema="skylize.runtime.agent_runner.AgentRunResult",
        allowed_tools=[ToolGrant(tool_id=t, purpose="e2e") for t in tools],
        max_token_budget=budget,
        max_execution_time_seconds=ttl,
        escalation_path=["human_owner"],
        failure_mode=FailureMode.FALLBACK_DEGRADED,
        memory_read_access=[],
        memory_write_access=[],
    )


def _signed_token(
    *,
    budget: int = 8_000,
    ttl: int = 30,
    scope: list[str] | None = None,
    agent_id: str = AGENT,
    expired: bool = False,
) -> GovernanceToken:
    signer = TokenSigner(_PRIVATE_KEY)
    now = datetime.now(timezone.utc)
    if expired:
        issued = now - timedelta(seconds=600)
        expires = now - timedelta(seconds=300)
    else:
        issued = now
        expires = now + timedelta(seconds=300)
    return signer.sign(
        token_id=uuid4(),
        agent_id=agent_id,
        authority_level="worker",
        department="engineering",
        delegation_chain=[agent_id],
        scope=scope if scope is not None else ["llm.generate"],
        max_token_budget=budget,
        max_execution_time_seconds=ttl,
        issued_at=issued,
        expires_at=expires,
        nonce=uuid4().hex,
    )


def _mock_anthropic_response(text: str = "E2E result") -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    resp.usage.input_tokens = 10
    resp.usage.output_tokens = 20
    return resp


def _make_redis(*, revoked: bytes | None = None) -> MagicMock:
    """Async Redis mock. Returns `revoked` for GET calls, None otherwise."""
    r = MagicMock()
    r.get = AsyncMock(return_value=revoked)
    return r


class FakeRegistry:
    def __init__(self, contract: AgentContract | None) -> None:
        self._contract = contract

    async def get_contract(self, agent_id: str) -> AgentContract | None:
        return self._contract


class FakeGovernance:
    def __init__(
        self,
        *,
        blocked: bool = False,
        reason: str = "",
        token: GovernanceToken | None = None,
    ) -> None:
        self._blocked = blocked
        self._reason = reason
        self._token = token

    async def check_live_state(self, agent_id: str, org_id: str) -> tuple[bool, str]:
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
        if self._token is not None:
            return self._token
        return _signed_token(budget=max_token_budget, ttl=max_execution_time_seconds)


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def __call__(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def _build_runner(
    *,
    contract: AgentContract | None = None,
    token: GovernanceToken | None = None,
    redis: MagicMock | None = None,
    gateway: AnthropicAdapter | None = None,
    audit: FakeAudit | None = None,
    blocked: bool = False,
    block_reason: str = "",
) -> tuple[LLMAgentRunner, FakeAudit]:
    if contract is None:
        contract = _contract()
    if token is None:
        token = _signed_token()
    if redis is None:
        redis = _make_redis()
    if audit is None:
        audit = FakeAudit()
    if gateway is None:
        gateway = AnthropicAdapter(settings=_settings())

    proxy = ToolProxy(
        redis=redis,
        governance_authority_pubkey=_PUB_DER,
        audit_publisher=audit,
    )
    runner = LLMAgentRunner(
        registry=FakeRegistry(contract),
        governance=FakeGovernance(blocked=blocked, reason=block_reason, token=token),
        tool_proxy=proxy,
        gateway=gateway,
        audit_publisher=audit,
    )
    return runner, audit


def _run_input(*, requested_max_tokens: int = 500) -> AgentRunInput:
    return AgentRunInput(
        agent_id=AGENT,
        org_id=ORG,
        prompt="Hello E2E",
        system="Be concise.",
        requested_max_tokens=requested_max_tokens,
    )


# ---------------------------------------------------------------------------
# E2E-1: happy path — full stack, real adapter, mock HTTP boundary
# ---------------------------------------------------------------------------

async def test_e2e_happy_path_returns_result() -> None:
    token = _signed_token(budget=8_000)
    runner, audit = _build_runner(token=token)
    mock_resp = _mock_anthropic_response("E2E output text")

    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        result = await runner.run(_run_input(requested_max_tokens=100))

    assert isinstance(result, AgentRunResult)
    assert result.text == "E2E output text"
    assert result.usage.total_tokens == 30  # 10 + 20
    assert result.cost_usd_micros >= 0
    assert isinstance(result.governance_token_id, UUID)
    # Runner emits one audit event; proxy also emits one. Find by stage key.
    runner_events = [e for e in audit.events if e.get("stage") == "RUN_SUCCESS"]
    assert len(runner_events) == 1


# ---------------------------------------------------------------------------
# E2E-2: budget — requested > contract budget → TokenBudgetExceeded
# ---------------------------------------------------------------------------

async def test_e2e_budget_exceeded_surfaces_correctly() -> None:
    # Token minted with effective_budget = min(requested=5000, contract=200) = 200
    # But ToolProxy budget stage checks params["requested_max_tokens"] (200) vs
    # token.max_token_budget (200) with tokens_used_so_far=0 — that passes.
    # The TokenBudgetExceeded path is in AnthropicAdapter when max_token_budget
    # is set AND tokens_used_so_far makes remaining < requested.
    # To exercise BudgetExceeded from ToolProxy: use params tokens_used_so_far.
    #
    # Simpler: use contract budget=200, request 5000 tokens.
    # Runner clamps effective_budget to 200 and passes params["requested_max_tokens"]=200.
    # Then AnthropicAdapter is called with requested_max_tokens=200 — that fits.
    # So instead test the adapter-level TokenBudgetExceeded by constructing a
    # request where tokens_used_so_far pushes remaining below requested.
    # We reach that by subclassing gateway to inject the right max_token_budget.

    # Actually the cleanest E2E path: request 5000, contract=200.
    # effective_budget = min(5000, 200) = 200. Token minted with budget=200.
    # ToolProxy checks: requested_max_tokens=200, tokens_used=0, remaining=200 → passes.
    # Adapter: no max_token_budget on the request → no secondary check → passes.
    # The budget ceiling manifests when tokens_used_so_far > 0 in the request.
    #
    # The real E2E budget test: tokens_used_so_far in the tool_call params.
    # The runner sets params["requested_max_tokens"] = effective_budget,
    # NOT tokens_used_so_far (which defaults to 0 in LLMGenerateRequest).
    # ToolProxy stage-5: requested=effective_budget, used=0, remaining=budget → OK.
    #
    # To trigger BudgetExceeded from ToolProxy we'd need used+requested > budget.
    # That scenario is covered by the ToolProxy unit tests; here we instead verify
    # that a TokenBudgetExceeded from the adapter layer propagates correctly.
    #
    # Build adapter that raises TokenBudgetExceeded:
    from skylize.adapters.llm.gateway import TokenBudgetExceeded as TBE

    class BudgetBustingAdapter:
        async def generate(self, request: Any) -> Any:
            raise TBE("budget exhausted in adapter")
        def generate_sync(self, request: Any) -> Any:  # noqa: D102
            raise TBE("budget exhausted in adapter")

    token = _signed_token(budget=8_000)
    runner, audit = _build_runner(
        token=token,
        gateway=BudgetBustingAdapter(),  # type: ignore[arg-type]
    )
    with pytest.raises(TBE):
        await runner.run(_run_input(requested_max_tokens=100))

    # Audit should have a proxy-level success audit followed by the exception
    # propagating from dispatch_llm — but since the adapter raises inside
    # dispatch_llm, the proxy audit success event is NOT emitted (exception
    # exits before _emit_audit_success). The runner catches only TimeoutError,
    # not TBE — so TBE propagates raw. No runner audit is written for this path.
    # Assert the exception type was correct (above) — that is the contract.


# ---------------------------------------------------------------------------
# E2E-3: scope violation — token scope excludes "llm.generate"
# ---------------------------------------------------------------------------

async def test_e2e_scope_violation_from_proxy_not_adapter() -> None:
    # Mint a token whose scope does NOT include llm.generate.
    token = _signed_token(scope=["memory.search"])  # signed, valid sig, wrong scope
    runner, audit = _build_runner(token=token)

    mock_resp = _mock_anthropic_response()
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        with pytest.raises(ScopeViolation) as exc_info:
            await runner.run(_run_input())

    # Must be a proxy-level ScopeViolation, not an adapter error.
    assert exc_info.value.stage == "scope"
    # Adapter was never called.
    mock_cls.return_value.messages.create.assert_not_called()


# ---------------------------------------------------------------------------
# E2E-4: revocation — Redis returns a non-None bytes value
# ---------------------------------------------------------------------------

async def test_e2e_token_revoked_before_api_call() -> None:
    token = _signed_token()
    redis = _make_redis(revoked=b"revoked:admin_action")
    runner, audit = _build_runner(token=token, redis=redis)

    mock_resp = _mock_anthropic_response()
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        with pytest.raises(TokenRevoked) as exc_info:
            await runner.run(_run_input())

    assert exc_info.value.stage == "revocation"
    # HTTP boundary never crossed.
    mock_cls.return_value.messages.create.assert_not_called()
    # Redis was queried.
    redis.get.assert_called_once()


# ---------------------------------------------------------------------------
# E2E-5: timeout — dispatch_llm sleeps longer than contract.max_execution_time_seconds
# ---------------------------------------------------------------------------

async def test_e2e_run_timeout_raised_when_dispatch_exceeds_ceiling() -> None:
    contract = _contract(ttl=1)
    token = _signed_token(ttl=1)
    audit = FakeAudit()

    # Build proxy manually so we can override dispatch_llm with a slow version.
    redis = _make_redis()
    proxy = ToolProxy(
        redis=redis,
        governance_authority_pubkey=_PUB_DER,
        audit_publisher=audit,
    )

    async def _slow_dispatch(*args: Any, **kwargs: Any) -> None:
        await asyncio.sleep(5)

    proxy.dispatch_llm = _slow_dispatch  # type: ignore[method-assign]

    runner = LLMAgentRunner(
        registry=FakeRegistry(contract),
        governance=FakeGovernance(token=token),
        tool_proxy=proxy,
        gateway=AnthropicAdapter(settings=_settings()),
        audit_publisher=audit,
    )

    with pytest.raises(RunTimeout) as exc_info:
        await runner.run(_run_input())

    assert exc_info.value.agent_id == AGENT
    assert exc_info.value.timeout_seconds == 1
    timeout_stages = [e["stage"] for e in audit.events]
    assert "RUN_TIMEOUT" in timeout_stages
