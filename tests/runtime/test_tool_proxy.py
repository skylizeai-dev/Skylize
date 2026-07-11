"""
ToolProxy unit tests — Redis-backed six-stage validation gate.

Covers:
  - valid token → all six stages pass, gateway called, audit emitted
  - bad signature → SignatureInvalid; steps 2-6 not reached
  - expired token → TokenExpired at step 2
  - revoked (Redis returns value) → TokenRevoked at step 3
  - not in token.scope → ScopeViolation step 4
  - in token.scope but not contract → ScopeViolation step 4
  - over budget → BudgetExceeded step 5
  - non-monotonic / empty delegation chain → DelegationInvalid step 6
  - audit_publisher failure does NOT propagate
  - dispatch_llm happy path → gateway.generate called + audit emitted
  - dispatch_llm fail → gateway.generate NOT called
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
)
from skylize.contracts.base import GovernanceToken
from skylize.contracts.token import TokenSigner
from skylize.runtime import (
    BudgetExceeded,
    DelegationInvalid,
    ScopeViolation,
    SignatureInvalid,
    ToolCallRequest,
    ToolProxy,
    TokenExpired,
    TokenRevoked,
)
from skylize.security.ecc_service import Curve, ECCService

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

AGENT = "hook_generator_agent"
ORG = "org_test"
TOOL = "llm.generate"
CONTRACT_TOOLS = [TOOL, "memory.search"]


def _keypair():
    return ECCService.generate_key_pair(Curve.P384)


def _signer_pubkey_bytes():
    pair = _keypair()
    return TokenSigner(pair.private_key), pair.public_key.public_bytes(
        encoding=Encoding.DER,
        format=PublicFormat.SubjectPublicKeyInfo,
    )


def _mint(
    signer: TokenSigner,
    *,
    scope: list[str] | None = None,
    budget: int = 8_000,
    expired: bool = False,
    agent_id: str = AGENT,
    delegation_chain: list[str] | None = None,
) -> GovernanceToken:
    now = datetime.now(timezone.utc)
    expires = now - timedelta(seconds=1) if expired else now + timedelta(seconds=300)
    return signer.sign(
        token_id=uuid4(),
        agent_id=agent_id,
        authority_level="worker",
        department="creative",
        delegation_chain=delegation_chain if delegation_chain is not None else ["vp_creative", "copy_director", agent_id],
        scope=scope if scope is not None else [TOOL, "memory.search"],
        max_token_budget=budget,
        max_execution_time_seconds=60,
        issued_at=now,
        expires_at=expires,
        nonce=uuid4().hex,
    )


def _tool_call(
    token: GovernanceToken,
    *,
    tool_id: str = TOOL,
    requested_max_tokens: int = 100,
    tokens_used_so_far: int = 0,
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_id=tool_id,
        governance_token_id=token.token_id,
        org_id=ORG,
        params={
            "prompt": "generate hooks",
            "requested_max_tokens": requested_max_tokens,
            "tokens_used_so_far": tokens_used_so_far,
        },
    )


def _redis_mock(revoked_value: bytes | None = None) -> AsyncMock:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=revoked_value)
    return redis


def _audit_mock() -> AsyncMock:
    return AsyncMock()


def _make_proxy(pubkey_bytes: bytes, redis=None, audit=None) -> ToolProxy:
    return ToolProxy(
        redis=redis or _redis_mock(),
        governance_authority_pubkey=pubkey_bytes,
        audit_publisher=audit or _audit_mock(),
    )


def _fake_gateway(
    text: str = "ok",
    total_tokens: int = 50,
) -> MagicMock:
    gateway = MagicMock()
    response = LLMGenerateResponse(
        text=text,
        provider="fake",
        concrete_model="fake-1",
        usage=LLMUsage(
            prompt_tokens=10,
            completion_tokens=total_tokens - 10,
            total_tokens=total_tokens,
        ),
        cost_usd_micros=0,
    )
    gateway.generate = AsyncMock(return_value=response)
    return gateway


# ---------------------------------------------------------------------------
# Tests: validate_token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_token_passes_all_stages() -> None:
    signer, pubkey_bytes = _signer_pubkey_bytes()
    token = _mint(signer)
    proxy = _make_proxy(pubkey_bytes)
    # Should not raise
    await proxy.validate_token(token, _tool_call(token), CONTRACT_TOOLS)


@pytest.mark.asyncio
async def test_bad_signature_raises_signature_invalid() -> None:
    signer, pubkey_bytes = _signer_pubkey_bytes()
    # Mint with a DIFFERENT key pair → signature won't verify against pubkey_bytes
    other_pair = ECCService.generate_key_pair(Curve.P384)
    bad_signer = TokenSigner(other_pair.private_key)
    token = _mint(bad_signer)

    audit = _audit_mock()
    proxy = _make_proxy(pubkey_bytes, audit=audit)

    with pytest.raises(SignatureInvalid) as exc_info:
        await proxy.validate_token(token, _tool_call(token), CONTRACT_TOOLS)

    assert exc_info.value.stage == "signature"
    audit.assert_called_once()


@pytest.mark.asyncio
async def test_bad_signature_steps_2_to_6_not_reached() -> None:
    """Redis.get must NOT be called when signature fails."""
    signer, pubkey_bytes = _signer_pubkey_bytes()
    other_pair = ECCService.generate_key_pair(Curve.P384)
    bad_signer = TokenSigner(other_pair.private_key)
    token = _mint(bad_signer)

    redis = _redis_mock()
    proxy = _make_proxy(pubkey_bytes, redis=redis)

    with pytest.raises(SignatureInvalid):
        await proxy.validate_token(token, _tool_call(token), CONTRACT_TOOLS)

    redis.get.assert_not_called()


@pytest.mark.asyncio
async def test_expired_token_raises_token_expired() -> None:
    signer, pubkey_bytes = _signer_pubkey_bytes()
    token = _mint(signer, expired=True)

    audit = _audit_mock()
    proxy = _make_proxy(pubkey_bytes, audit=audit)

    with pytest.raises(TokenExpired) as exc_info:
        await proxy.validate_token(token, _tool_call(token), CONTRACT_TOOLS)

    assert exc_info.value.stage == "expiry"
    audit.assert_called_once()


@pytest.mark.asyncio
async def test_revoked_token_raises_token_revoked() -> None:
    signer, pubkey_bytes = _signer_pubkey_bytes()
    token = _mint(signer)

    redis = _redis_mock(revoked_value=b"circuit_breaker_tripped")
    audit = _audit_mock()
    proxy = _make_proxy(pubkey_bytes, redis=redis, audit=audit)

    with pytest.raises(TokenRevoked) as exc_info:
        await proxy.validate_token(token, _tool_call(token), CONTRACT_TOOLS)

    assert exc_info.value.stage == "revocation"
    redis.get.assert_called_once_with(f"skylize:revoked:{token.token_id}")
    audit.assert_called_once()


@pytest.mark.asyncio
async def test_tool_not_in_token_scope_raises_scope_violation() -> None:
    signer, pubkey_bytes = _signer_pubkey_bytes()
    token = _mint(signer, scope=["memory.search"])  # llm.generate NOT in scope

    audit = _audit_mock()
    proxy = _make_proxy(pubkey_bytes, audit=audit)

    with pytest.raises(ScopeViolation) as exc_info:
        await proxy.validate_token(token, _tool_call(token, tool_id=TOOL), CONTRACT_TOOLS)

    assert exc_info.value.stage == "scope"
    assert "not in token scope" in exc_info.value.reason
    audit.assert_called_once()


@pytest.mark.asyncio
async def test_tool_in_token_scope_but_not_contract_raises_scope_violation() -> None:
    signer, pubkey_bytes = _signer_pubkey_bytes()
    token = _mint(signer, scope=[TOOL, "memory.search"])

    contract_without_tool = ["memory.search"]  # llm.generate not in contract

    audit = _audit_mock()
    proxy = _make_proxy(pubkey_bytes, audit=audit)

    with pytest.raises(ScopeViolation) as exc_info:
        await proxy.validate_token(token, _tool_call(token, tool_id=TOOL), contract_without_tool)

    assert exc_info.value.stage == "scope"
    assert "not in contract" in exc_info.value.reason
    audit.assert_called_once()


@pytest.mark.asyncio
async def test_over_budget_raises_budget_exceeded() -> None:
    signer, pubkey_bytes = _signer_pubkey_bytes()
    token = _mint(signer, budget=100)

    audit = _audit_mock()
    proxy = _make_proxy(pubkey_bytes, audit=audit)

    # Request 200 tokens on a 100-token budget
    tool_call = _tool_call(token, requested_max_tokens=200, tokens_used_so_far=0)

    with pytest.raises(BudgetExceeded) as exc_info:
        await proxy.validate_token(token, tool_call, CONTRACT_TOOLS)

    assert exc_info.value.stage == "budget"
    audit.assert_called_once()


@pytest.mark.asyncio
async def test_non_monotonic_delegation_chain_raises_delegation_invalid() -> None:
    signer, pubkey_bytes = _signer_pubkey_bytes()
    # Chain doesn't end with the agent_id
    token = _mint(signer, delegation_chain=["vp_creative", "other_agent"])

    audit = _audit_mock()
    proxy = _make_proxy(pubkey_bytes, audit=audit)

    with pytest.raises(DelegationInvalid) as exc_info:
        await proxy.validate_token(token, _tool_call(token), CONTRACT_TOOLS)

    assert exc_info.value.stage == "delegation"
    audit.assert_called_once()


@pytest.mark.asyncio
async def test_empty_delegation_chain_raises_delegation_invalid() -> None:
    signer, pubkey_bytes = _signer_pubkey_bytes()
    token = _mint(signer, delegation_chain=[])

    audit = _audit_mock()
    proxy = _make_proxy(pubkey_bytes, audit=audit)

    with pytest.raises(DelegationInvalid) as exc_info:
        await proxy.validate_token(token, _tool_call(token), CONTRACT_TOOLS)

    assert exc_info.value.stage == "delegation"


@pytest.mark.asyncio
async def test_audit_publisher_failure_does_not_propagate() -> None:
    signer, pubkey_bytes = _signer_pubkey_bytes()
    other_pair = ECCService.generate_key_pair(Curve.P384)
    bad_signer = TokenSigner(other_pair.private_key)
    token = _mint(bad_signer)

    async def exploding_publisher(event: dict) -> None:
        raise RuntimeError("audit bus down")

    proxy = _make_proxy(pubkey_bytes, audit=exploding_publisher)

    # Should raise SignatureInvalid, NOT RuntimeError from the publisher
    with pytest.raises(SignatureInvalid):
        await proxy.validate_token(token, _tool_call(token), CONTRACT_TOOLS)


# ---------------------------------------------------------------------------
# Tests: dispatch_llm
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_llm_happy_path() -> None:
    signer, pubkey_bytes = _signer_pubkey_bytes()
    token = _mint(signer)

    audit = _audit_mock()
    gateway = _fake_gateway(total_tokens=50)
    proxy = _make_proxy(pubkey_bytes, audit=audit)

    tool_call = _tool_call(token, requested_max_tokens=200)
    response = await proxy.dispatch_llm(token, tool_call, CONTRACT_TOOLS, gateway)

    assert response.usage.total_tokens == 50
    gateway.generate.assert_called_once()
    # Both validate-deny (none) and success audit should result in one call
    audit.assert_called_once()
    event = audit.call_args[0][0]
    assert event["result"] == "success"
    assert event["tool_id"] == TOOL


@pytest.mark.asyncio
async def test_dispatch_llm_validation_fail_gateway_not_called() -> None:
    signer, pubkey_bytes = _signer_pubkey_bytes()
    token = _mint(signer, expired=True)

    gateway = _fake_gateway()
    proxy = _make_proxy(pubkey_bytes)

    with pytest.raises(TokenExpired):
        await proxy.dispatch_llm(token, _tool_call(token), CONTRACT_TOOLS, gateway)

    gateway.generate.assert_not_called()
