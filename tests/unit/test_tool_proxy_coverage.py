"""
Supplemental tests targeting uncovered branches in tool_proxy.py:

- Line 197: naive-datetime expiry normalization (token.expires_at without tzinfo)
- Lines 320-321: audit publisher failure swallowed in _emit_audit_success
- Lines 349/352-354: LLMGenerateHandler.handle round-trip
- Lines 361-362: MemorySearchHandler.handle stub
- Lines 420-422: RegistryToolProxy → AgentNotRegistered path
- Lines 433-435: RegistryToolProxy → RunExpired from ledger
- Lines 461-463: RegistryToolProxy → no handler registered
- Lines 484-489: RegistryToolProxy → TokenBudgetExceeded from ledger debit
- Line 544: _hash(None) returns None
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from skylize.adapters.llm.gateway import (
    LLMGenerateResponse,
    LLMUsage,
    TokenBudgetExceeded,
)
from skylize.contracts.base import (
    AgentContract,
    FailureMode,
    ToolGrant,
)
from skylize.contracts.registry import AgentNotRegistered
from skylize.contracts.token import TokenSigner
from skylize.runtime.run_ledger import RunExpired
from skylize.runtime.tool_proxy import (
    LLMGenerateHandler,
    MemorySearchHandler,
    RegistryToolProxy,
    ToolCallRequest,
    ToolDispatchDenied,
    ToolProxy,
    TokenExpired,
    _hash,
)
from skylize.security.ecc_service import Curve, ECCService

AGENT = "test_agent"
ORG = "org_unit"
TOOL = "llm.generate"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _keypair():
    return ECCService.generate_key_pair(Curve.P384)


def _signer_pubkey_bytes():
    pair = _keypair()
    return (
        TokenSigner(pair.private_key),
        pair.public_key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo),
    )


def _mint(signer, *, expired_naive: bool = False, budget: int = 8_000):
    now = datetime.now(timezone.utc)
    if expired_naive:
        # Strip tzinfo so the naive-tz branch is hit
        expires = (now - timedelta(seconds=1)).replace(tzinfo=None)
    else:
        expires = now + timedelta(seconds=300)
    return signer.sign(
        token_id=uuid4(),
        agent_id=AGENT,
        authority_level="worker",
        department="engineering",
        delegation_chain=[AGENT],
        scope=[TOOL],
        max_token_budget=budget,
        max_execution_time_seconds=60,
        issued_at=now.replace(tzinfo=None) if expired_naive else now,
        expires_at=expires,
        nonce=uuid4().hex,
    )


def _tool_call(token, requested: int = 100):
    return ToolCallRequest(
        tool_id=TOOL,
        governance_token_id=token.token_id,
        org_id=ORG,
        params={"requested_max_tokens": requested, "prompt": "hi"},
    )


def _redis_mock(revoked=None):
    r = AsyncMock()
    r.get = AsyncMock(return_value=revoked)
    return r


def _proxy(pubkey_bytes, *, redis=None, audit=None):
    return ToolProxy(
        redis=redis or _redis_mock(),
        governance_authority_pubkey=pubkey_bytes,
        audit_publisher=audit or AsyncMock(),
    )


def _contract(tools=(TOOL,), budget=8_000):
    return AgentContract(
        agent_id=AGENT,
        agent_role="Test",
        authority_level="worker",
        department="engineering",
        input_schema="skylize.runtime.agent_runner.AgentRunInput",
        output_schema="skylize.runtime.agent_runner.AgentRunResult",
        allowed_tools=[ToolGrant(tool_id=t, purpose="test") for t in tools],
        max_token_budget=budget,
        max_execution_time_seconds=60,
        escalation_path=["human_owner"],
        failure_mode=FailureMode.FALLBACK_DEGRADED,
        memory_read_access=[],
        memory_write_access=[],
    )


def _gov_token(signer, budget=8_000):
    now = datetime.now(timezone.utc)
    return signer.sign(
        token_id=uuid4(),
        agent_id=AGENT,
        authority_level="worker",
        department="engineering",
        delegation_chain=[AGENT],
        scope=[TOOL],
        max_token_budget=budget,
        max_execution_time_seconds=60,
        issued_at=now,
        expires_at=now + timedelta(seconds=300),
        nonce=uuid4().hex,
    )


# ---------------------------------------------------------------------------
# Line 197: naive-datetime expiry normalization
# ---------------------------------------------------------------------------

async def test_naive_expires_at_is_normalized_and_expired_raises() -> None:
    """expires_at without tzinfo → branch line 197 hit, then TokenExpired raised."""
    signer, pubkey_bytes = _signer_pubkey_bytes()
    now = datetime.now(timezone.utc)
    # Sign using naive datetimes — _iso() in canonical_signing_bytes normalizes them,
    # so the signature is still valid; but expires_at is naive and in the past.
    past_naive = (now - timedelta(seconds=10)).replace(tzinfo=None)
    issued_naive = now.replace(tzinfo=None)
    token = signer.sign(
        token_id=uuid4(),
        agent_id=AGENT,
        authority_level="worker",
        department="engineering",
        delegation_chain=[AGENT],
        scope=[TOOL],
        max_token_budget=8_000,
        max_execution_time_seconds=60,
        issued_at=issued_naive,
        expires_at=past_naive,
        nonce=uuid4().hex,
    )

    proxy = _proxy(pubkey_bytes)
    with pytest.raises(TokenExpired):
        await proxy.validate_token(token, _tool_call(token), [TOOL])


# ---------------------------------------------------------------------------
# Lines 320-321: audit success publisher exception is swallowed
# ---------------------------------------------------------------------------

async def test_audit_success_exception_swallowed_in_dispatch_llm() -> None:
    signer, pubkey_bytes = _signer_pubkey_bytes()
    token = _gov_token(signer)

    calls: list[dict] = []

    async def flaky_publisher(event: dict) -> None:
        if event.get("result") == "success":
            raise RuntimeError("audit bus exploded on success")
        calls.append(event)

    gateway = MagicMock()
    gateway.generate = AsyncMock(
        return_value=LLMGenerateResponse(
            text="hi",
            provider="fake",
            concrete_model="fake-1",
            usage=LLMUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10),
            cost_usd_micros=0,
        )
    )

    proxy = _proxy(pubkey_bytes, audit=flaky_publisher)
    # Must not raise despite the publisher exploding on success
    result = await proxy.dispatch_llm(token, _tool_call(token), [TOOL], gateway)
    assert result.text == "hi"


# ---------------------------------------------------------------------------
# Lines 349/352-354: LLMGenerateHandler.handle
# ---------------------------------------------------------------------------

async def test_llm_generate_handler_routes_to_gateway() -> None:
    gateway = MagicMock()
    gateway.generate = AsyncMock(
        return_value=LLMGenerateResponse(
            text="from gateway",
            provider="fake",
            concrete_model="fake-1",
            usage=LLMUsage(prompt_tokens=3, completion_tokens=7, total_tokens=10),
            cost_usd_micros=5,
        )
    )
    handler = LLMGenerateHandler(gateway)
    payload = {
        "prompt": "hello",
        "requested_max_tokens": 100,
        "governance_token_id": str(uuid4()),
        "org_id": ORG,
    }
    result = await handler.handle(payload, ORG)
    assert result["text"] == "from gateway"
    assert result["usage"]["total_tokens"] == 10
    gateway.generate.assert_called_once()


# ---------------------------------------------------------------------------
# Lines 361-362: MemorySearchHandler.handle stub
# ---------------------------------------------------------------------------

async def test_memory_search_handler_returns_empty_results() -> None:
    handler = MemorySearchHandler()
    result = await handler.handle({"query": "foo"}, ORG)
    assert result == {"results": []}


# ---------------------------------------------------------------------------
# Lines 420-422: RegistryToolProxy → AgentNotRegistered
# ---------------------------------------------------------------------------

async def test_registry_proxy_agent_not_registered_raises_dispatch_denied() -> None:
    from skylize.contracts.token import AllowAllLiveState
    from skylize.runtime.run_ledger import InMemoryRunLedger

    pair = _keypair()
    signer = TokenSigner(pair.private_key)
    token = _gov_token(signer)

    # Registry that always raises AgentNotRegistered
    registry = MagicMock()
    registry.resolve.side_effect = AgentNotRegistered(AGENT)

    bus = AsyncMock()
    bus.publish = AsyncMock()

    ledger = InMemoryRunLedger()
    proxy = RegistryToolProxy(
        registry=registry,
        public_key=pair.public_key,
        live_state_for=lambda org_id: AllowAllLiveState(),
        bus=bus,
        run_ledger=ledger,
        handlers={TOOL: MagicMock()},
    )

    with pytest.raises(ToolDispatchDenied):
        await proxy.dispatch(
            token=token,
            tool_id=TOOL,
            payload={"requested_max_tokens": 100, "prompt": "hi"},
            org_id=ORG,
        )


# ---------------------------------------------------------------------------
# Lines 433-435: RegistryToolProxy → RunExpired from ledger.open_run
# ---------------------------------------------------------------------------

async def test_registry_proxy_run_expired_propagates() -> None:
    from skylize.contracts.token import AllowAllLiveState

    pair = _keypair()
    signer = TokenSigner(pair.private_key)
    token = _gov_token(signer)

    registry = MagicMock()
    registry.resolve.return_value = _contract()

    bus = AsyncMock()
    bus.publish = AsyncMock()

    ledger = MagicMock()
    ledger.open_run = AsyncMock(side_effect=RunExpired("run expired"))
    ledger.used = AsyncMock(return_value=0)

    proxy = RegistryToolProxy(
        registry=registry,
        public_key=pair.public_key,
        live_state_for=lambda org_id: AllowAllLiveState(),
        bus=bus,
        run_ledger=ledger,
        handlers={TOOL: MagicMock()},
    )

    with pytest.raises(RunExpired):
        await proxy.dispatch(
            token=token,
            tool_id=TOOL,
            payload={"requested_max_tokens": 100, "prompt": "hi"},
            org_id=ORG,
        )


# ---------------------------------------------------------------------------
# Lines 461-463: RegistryToolProxy → no handler registered
# ---------------------------------------------------------------------------

async def test_registry_proxy_no_handler_raises_dispatch_denied() -> None:
    from skylize.contracts.token import AllowAllLiveState
    from skylize.runtime.run_ledger import InMemoryRunLedger

    pair = _keypair()
    signer = TokenSigner(pair.private_key)
    token = _gov_token(signer)

    registry = MagicMock()
    registry.resolve.return_value = _contract()

    bus = AsyncMock()
    bus.publish = AsyncMock()

    ledger = InMemoryRunLedger()

    proxy = RegistryToolProxy(
        registry=registry,
        public_key=pair.public_key,
        live_state_for=lambda org_id: AllowAllLiveState(),
        bus=bus,
        run_ledger=ledger,
        handlers={},  # no handler for TOOL
    )

    with pytest.raises(ToolDispatchDenied, match="no handler registered"):
        await proxy.dispatch(
            token=token,
            tool_id=TOOL,
            payload={"requested_max_tokens": 100, "prompt": "hi"},
            org_id=ORG,
        )


# ---------------------------------------------------------------------------
# Lines 484-489: RegistryToolProxy → TokenBudgetExceeded from ledger.debit
# ---------------------------------------------------------------------------

async def test_registry_proxy_ledger_debit_budget_exceeded_propagates() -> None:
    from skylize.contracts.token import AllowAllLiveState

    pair = _keypair()
    signer = TokenSigner(pair.private_key)
    token = _gov_token(signer)

    registry = MagicMock()
    registry.resolve.return_value = _contract()

    bus = AsyncMock()
    bus.publish = AsyncMock()

    handler = AsyncMock()
    handler.handle = AsyncMock(return_value={"usage": {"total_tokens": 100}})

    ledger = MagicMock()
    ledger.open_run = AsyncMock()
    ledger.used = AsyncMock(return_value=0)
    ledger.debit = AsyncMock(side_effect=TokenBudgetExceeded("over budget"))

    proxy = RegistryToolProxy(
        registry=registry,
        public_key=pair.public_key,
        live_state_for=lambda org_id: AllowAllLiveState(),
        bus=bus,
        run_ledger=ledger,
        handlers={TOOL: handler},
    )

    with pytest.raises(TokenBudgetExceeded):
        await proxy.dispatch(
            token=token,
            tool_id=TOOL,
            payload={"requested_max_tokens": 100, "prompt": "hi"},
            org_id=ORG,
        )


# ---------------------------------------------------------------------------
# Line 544: _hash(None)
# ---------------------------------------------------------------------------

def test_hash_none_returns_none() -> None:
    assert _hash(None) is None


def test_hash_value_returns_hex_string() -> None:
    result = _hash({"key": "value"})
    assert isinstance(result, str)
    assert len(result) == 64  # sha256 hex


def test_actual_tokens_no_usage_key_returns_zero() -> None:
    from skylize.runtime.tool_proxy import _actual_tokens
    # No 'usage' key → branch line 544 (return 0)
    assert _actual_tokens({"text": "hi"}) == 0
    # usage exists but is a scalar, not a Mapping
    assert _actual_tokens({"usage": 42}) == 0
