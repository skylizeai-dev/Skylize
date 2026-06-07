"""Governance Authority: mint, validate, revoke, circuit breaker, kill switch."""

from __future__ import annotations

from uuid import uuid4

import pytest

from skylize.app.audit.service import AuditService
from skylize.app.governance import CIRCUIT_BREAKER_THRESHOLD, GovernanceAuthority, GovernanceDenied
from skylize.config import Settings
from skylize.contracts.registry import MVP_REGISTRY
from skylize.contracts.token import validate_tool_call, verify_token_signature
from skylize.dal.memory import InMemoryAuditRepository, InMemoryGovernanceRepository
from skylize.events.memory_bus import InMemoryEventBus

ORG = "org_test"


def _authority():
    bus = InMemoryEventBus()
    audit = AuditService(bus, InMemoryAuditRepository())
    authority = GovernanceAuthority.build(
        repo=InMemoryGovernanceRepository(), audit=audit, bus=bus,
        registry=MVP_REGISTRY, settings=Settings(backend="memory"),
    )
    return authority, bus


async def test_mint_signs_persists_and_emits() -> None:
    authority, bus = _authority()
    contract = MVP_REGISTRY.resolve("hook_generator_agent")
    corr = uuid4()
    token = await authority.mint(contract, org_id=ORG, correlation_id=corr)

    assert verify_token_signature(token, authority.public_key)
    assert token.agent_id == "hook_generator_agent"
    assert set(token.scope) == {"llm.generate", "memory.search"}
    assert bus.published_of_type("governance.token_issued")
    assert bus.published_of_type("audit.action_recorded")


async def test_minted_token_validates_then_fails_after_revoke() -> None:
    authority, _ = _authority()
    contract = MVP_REGISTRY.resolve("hook_generator_agent")
    corr = uuid4()
    token = await authority.mint(contract, org_id=ORG, correlation_id=corr)
    allowed = {t.tool_id for t in contract.allowed_tools}

    ok = validate_tool_call(
        token=token, public_key=authority.public_key,
        requested_tool_id="llm.generate", contract_allowed_tool_ids=allowed,
        requested_token_cost=100, tokens_used_so_far=0,
        live_state=authority.live_state_checker(ORG),
    )
    assert ok.is_valid

    await authority.revoke(
        token_id=token.token_id, agent_id=token.agent_id, org_id=ORG,
        reason="manual", correlation_id=corr,
    )
    after = validate_tool_call(
        token=token, public_key=authority.public_key,
        requested_tool_id="llm.generate", contract_allowed_tool_ids=allowed,
        requested_token_cost=100, tokens_used_so_far=0,
        live_state=authority.live_state_checker(ORG),
    )
    assert not after.is_valid
    assert after.failed_stage.value == "revocation"


async def test_circuit_breaker_trips_at_threshold() -> None:
    authority, _ = _authority()
    corr = uuid4()
    tripped = False
    for _ in range(CIRCUIT_BREAKER_THRESHOLD):
        tripped = await authority.record_violation(
            agent_id="hook_generator_agent", org_id=ORG,
            reason="scope", correlation_id=corr,
        )
    assert tripped is True
    with pytest.raises(GovernanceDenied):
        await authority.assert_active("hook_generator_agent", ORG)


async def test_kill_switch_tenant_scope_blocks_all_agents() -> None:
    authority, _ = _authority()
    corr = uuid4()
    await authority.engage_kill_switch(
        scope_type="tenant", scope_id=ORG, org_id=ORG,
        engaged_by="user_owner", reason="incident", correlation_id=corr,
    )
    with pytest.raises(GovernanceDenied):
        await authority.assert_active("hook_generator_agent", ORG)
    # A different tenant is unaffected.
    await authority.assert_active("hook_generator_agent", "org_other")

    await authority.disengage_kill_switch(
        scope_type="tenant", scope_id=ORG, org_id=ORG,
        disengaged_by="user_owner", correlation_id=corr,
    )
    await authority.assert_active("hook_generator_agent", ORG)  # cleared


async def test_kill_switch_agent_scope_is_targeted() -> None:
    authority, _ = _authority()
    corr = uuid4()
    await authority.engage_kill_switch(
        scope_type="agent", scope_id="hook_generator_agent", org_id=ORG,
        engaged_by="user_owner", reason="rogue", correlation_id=corr,
    )
    with pytest.raises(GovernanceDenied):
        await authority.assert_active("hook_generator_agent", ORG)
    await authority.assert_active("ad_copy_agent", ORG)  # other agent fine
