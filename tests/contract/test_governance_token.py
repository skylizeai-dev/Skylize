"""
Governance token foundation tests (ECDSA P-384).

Proves: a signed token verifies; tampering breaks verification; the ordered
validation pipeline accepts a valid call and rejects at the correct stage for
expiry, scope, budget, and delegation failures.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4


from skylize.contracts.token import (
    AllowAllLiveState,
    TokenSigner,
    ValidationStage,
    validate_tool_call,
    verify_token_signature,
)
from skylize.security.ecc_service import Curve, ECCService

CONTRACT_TOOLS = {"llm.generate", "memory.search"}


def _signer_and_pub():
    pair = ECCService.generate_key_pair(Curve.P384)
    return TokenSigner(pair.private_key), pair.public_key


def _mint(signer, *, scope, agent_id="hook_generator_agent", budget=8000, ttl_s=300):
    now = datetime.now(timezone.utc)
    return signer.sign(
        token_id=uuid4(),
        agent_id=agent_id,
        authority_level="worker",
        department="creative",
        delegation_chain=["vp_creative", "copy_director", agent_id],
        scope=scope,
        max_token_budget=budget,
        max_execution_time_seconds=60,
        issued_at=now,
        expires_at=now + timedelta(seconds=ttl_s),
        nonce=uuid4().hex,
    )


def test_signed_token_verifies() -> None:
    signer, pub = _signer_and_pub()
    token = _mint(signer, scope=["llm.generate", "memory.search"])
    assert verify_token_signature(token, pub) is True


def test_tampered_token_fails_signature() -> None:
    signer, pub = _signer_and_pub()
    token = _mint(signer, scope=["llm.generate"])
    forged = token.model_copy(update={"max_token_budget": 999_999})
    assert verify_token_signature(forged, pub) is False


def test_wrong_key_fails_signature() -> None:
    signer, _ = _signer_and_pub()
    _, other_pub = _signer_and_pub()
    token = _mint(signer, scope=["llm.generate"])
    assert verify_token_signature(token, other_pub) is False


def test_full_validation_happy_path() -> None:
    signer, pub = _signer_and_pub()
    token = _mint(signer, scope=["llm.generate", "memory.search"])
    result = validate_tool_call(
        token=token,
        public_key=pub,
        requested_tool_id="llm.generate",
        contract_allowed_tool_ids=CONTRACT_TOOLS,
        requested_token_cost=1000,
        tokens_used_so_far=0,
        live_state=AllowAllLiveState(),
    )
    assert result.is_valid, result.reason


def test_expired_token_rejected_at_expiry_stage() -> None:
    signer, pub = _signer_and_pub()
    token = _mint(signer, scope=["llm.generate"], ttl_s=-1)
    result = validate_tool_call(
        token=token,
        public_key=pub,
        requested_tool_id="llm.generate",
        contract_allowed_tool_ids=CONTRACT_TOOLS,
        requested_token_cost=10,
        tokens_used_so_far=0,
        live_state=AllowAllLiveState(),
    )
    assert not result.is_valid
    assert result.failed_stage is ValidationStage.EXPIRY


def test_out_of_scope_tool_rejected_at_scope_stage() -> None:
    signer, pub = _signer_and_pub()
    token = _mint(signer, scope=["memory.search"])  # no llm.generate
    result = validate_tool_call(
        token=token,
        public_key=pub,
        requested_tool_id="llm.generate",
        contract_allowed_tool_ids=CONTRACT_TOOLS,
        requested_token_cost=10,
        tokens_used_so_far=0,
        live_state=AllowAllLiveState(),
    )
    assert not result.is_valid
    assert result.failed_stage is ValidationStage.SCOPE


def test_scope_not_subset_of_contract_rejected() -> None:
    signer, pub = _signer_and_pub()
    token = _mint(signer, scope=["llm.generate", "bi.query"])  # bi.query not granted
    result = validate_tool_call(
        token=token,
        public_key=pub,
        requested_tool_id="llm.generate",
        contract_allowed_tool_ids=CONTRACT_TOOLS,
        requested_token_cost=10,
        tokens_used_so_far=0,
        live_state=AllowAllLiveState(),
    )
    assert not result.is_valid
    assert result.failed_stage is ValidationStage.SCOPE


def test_budget_overrun_rejected_at_budget_stage() -> None:
    signer, pub = _signer_and_pub()
    token = _mint(signer, scope=["llm.generate"], budget=5000)
    result = validate_tool_call(
        token=token,
        public_key=pub,
        requested_tool_id="llm.generate",
        contract_allowed_tool_ids=CONTRACT_TOOLS,
        requested_token_cost=4000,
        tokens_used_so_far=2000,  # 6000 > 5000
        live_state=AllowAllLiveState(),
    )
    assert not result.is_valid
    assert result.failed_stage is ValidationStage.BUDGET


def test_revocation_via_live_state_rejected() -> None:
    signer, pub = _signer_and_pub()
    token = _mint(signer, scope=["llm.generate"])

    class Revoked:
        def revocation_reason(self, token_id, agent_id):  # noqa: ANN001
            return "agent suspended by circuit breaker"

    result = validate_tool_call(
        token=token,
        public_key=pub,
        requested_tool_id="llm.generate",
        contract_allowed_tool_ids=CONTRACT_TOOLS,
        requested_token_cost=10,
        tokens_used_so_far=0,
        live_state=Revoked(),
    )
    assert not result.is_valid
    assert result.failed_stage is ValidationStage.REVOCATION


def test_malformed_delegation_chain_rejected() -> None:
    signer, pub = _signer_and_pub()
    now = datetime.now(timezone.utc)
    token = signer.sign(
        token_id=uuid4(),
        agent_id="hook_generator_agent",
        authority_level="worker",
        department="creative",
        delegation_chain=["vp_creative"],  # does not end at the agent
        scope=["llm.generate"],
        max_token_budget=8000,
        max_execution_time_seconds=60,
        issued_at=now,
        expires_at=now + timedelta(seconds=300),
        nonce=uuid4().hex,
    )
    result = validate_tool_call(
        token=token,
        public_key=pub,
        requested_tool_id="llm.generate",
        contract_allowed_tool_ids=CONTRACT_TOOLS,
        requested_token_cost=10,
        tokens_used_so_far=0,
        live_state=AllowAllLiveState(),
    )
    assert not result.is_valid
    assert result.failed_stage is ValidationStage.DELEGATION
