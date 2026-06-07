"""
Governance state synchronization (Sprint-2 Task 2).

Proves the two properties the Sprint-1 audit found missing:

  1. Cross-process propagation — a revoke / suspend / kill on instance A is
     visible on instances B and C (they share a GovernanceBroadcast, exactly as
     separate pods share a Redis Pub/Sub channel in production).
  2. Startup rehydration — a freshly built Authority warms its in-memory snapshot
     from the DB system of record, so a restart never forgets an active kill.

These run on the in-memory broadcast + repo, which uphold the same ports as the
Redis/Postgres concretes; the Redis/Postgres equivalents are exercised by the
integration suite (Tasks 4/5).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from skylize.app.audit.service import AuditService
from skylize.app.governance import GovernanceAuthority, GovernanceDenied
from skylize.app.governance.broadcast import InMemoryGovernanceBroadcast
from skylize.config import Settings
from skylize.contracts.registry import MVP_REGISTRY
from skylize.contracts.token import validate_tool_call
from skylize.dal.memory import InMemoryAuditRepository, InMemoryGovernanceRepository
from skylize.events.memory_bus import InMemoryEventBus
from skylize.security.ecc_service import Curve, ECCService

ORG = "org_sync"

# A SINGLE shared signing key across all "pods" — this is the production
# invariant (Task 3). Without it, a token minted on A would fail signature
# verification on B/C and the revocation test would pass for the wrong reason.
_SHARED_KEY_PEM = ECCService.generate_key_pair(Curve.P384).private_pem().decode()


def _instance(repo, broadcast):
    """Build one Authority sharing a repo (DB), broadcast (pub/sub), and signing
    key — i.e. one pod in a multi-replica deployment."""
    bus = InMemoryEventBus()
    audit = AuditService(bus, InMemoryAuditRepository())
    return GovernanceAuthority.build(
        repo=repo, audit=audit, bus=bus, registry=MVP_REGISTRY,
        settings=Settings(backend="memory", governance_signing_key_pem=_SHARED_KEY_PEM),
        broadcast=broadcast,
    )


async def _three_instances():
    """Three Authorities sharing one DB and one broadcast (A/B/C), subscribers up."""
    repo = InMemoryGovernanceRepository()
    broadcast = InMemoryGovernanceBroadcast()
    a, b, c = (_instance(repo, broadcast) for _ in range(3))
    # In production each pod runs start_subscriber() as a background task; the
    # in-memory broadcast registers the handler synchronously.
    for inst in (a, b, c):
        await inst.start_subscriber()
    return a, b, c


async def test_revoke_on_A_is_seen_by_B_and_C() -> None:
    a, b, c = await _three_instances()
    contract = MVP_REGISTRY.resolve("hook_generator_agent")
    corr = uuid4()

    # A mints a token; the token validates on every instance (shared key).
    token = await a.mint(contract, org_id=ORG, correlation_id=corr)
    allowed = {t.tool_id for t in contract.allowed_tools}

    def _check(inst):
        return validate_tool_call(
            token=token, public_key=inst.public_key,
            requested_tool_id="llm.generate", contract_allowed_tool_ids=allowed,
            requested_token_cost=10, tokens_used_so_far=0,
            live_state=inst.live_state_checker(ORG),
        )

    assert _check(a).is_valid and _check(b).is_valid and _check(c).is_valid

    # A revokes. B and C must now reject the SAME token via their own snapshots.
    await a.revoke(token_id=token.token_id, agent_id=token.agent_id, org_id=ORG,
                   reason="compromise", correlation_id=corr)

    for inst in (a, b, c):
        res = _check(inst)
        assert not res.is_valid, "revocation did not propagate"
        assert res.failed_stage.value == "revocation"


async def test_tenant_kill_on_A_blocks_on_B_and_C() -> None:
    a, b, c = await _three_instances()
    await a.engage_kill_switch(
        scope_type="tenant", scope_id=ORG, org_id=ORG,
        engaged_by="owner", reason="incident", correlation_id=uuid4(),
    )
    # Every instance now denies the agent for this tenant.
    for inst in (a, b, c):
        with pytest.raises(GovernanceDenied):
            await inst.assert_active("hook_generator_agent", ORG)

    # Disengage on B propagates back to A and C.
    await b.disengage_kill_switch(
        scope_type="tenant", scope_id=ORG, org_id=ORG,
        disengaged_by="owner", correlation_id=uuid4(),
    )
    for inst in (a, b, c):
        await inst.assert_active("hook_generator_agent", ORG)  # cleared everywhere


async def test_platform_kill_on_A_blocks_all_instances_all_tenants() -> None:
    a, b, c = await _three_instances()
    await a.engage_kill_switch(
        scope_type="platform", scope_id="platform", org_id=ORG,
        engaged_by="owner", reason="systemic", correlation_id=uuid4(),
    )
    for inst in (a, b, c):
        with pytest.raises(GovernanceDenied):
            await inst.assert_active("hook_generator_agent", "any_org")


async def test_circuit_breaker_suspend_propagates() -> None:
    a, b, c = await _three_instances()
    corr = uuid4()
    # Drive A's breaker to the trip threshold; suspension must propagate.
    tripped = False
    from skylize.app.governance import CIRCUIT_BREAKER_THRESHOLD
    for _ in range(CIRCUIT_BREAKER_THRESHOLD):
        tripped = await a.record_violation(
            agent_id="hook_generator_agent", org_id=ORG, reason="scope",
            correlation_id=corr,
        )
    assert tripped
    for inst in (a, b, c):
        with pytest.raises(GovernanceDenied):
            await inst.assert_active("hook_generator_agent", ORG)


async def test_restart_rehydrates_active_kill_from_db() -> None:
    repo = InMemoryGovernanceRepository()
    broadcast = InMemoryGovernanceBroadcast()

    # Instance A engages a tenant kill (persisted to the shared repo/DB).
    a = _instance(repo, broadcast)
    await a.start_subscriber()
    await a.engage_kill_switch(
        scope_type="tenant", scope_id=ORG, org_id=ORG,
        engaged_by="owner", reason="incident", correlation_id=uuid4(),
    )

    # A brand-new instance D is built later (a restart / new pod) against the
    # SAME DB but a FRESH broadcast — it missed the live message. Rehydrate must
    # restore the kill from the system of record.
    d = _instance(repo, InMemoryGovernanceBroadcast())
    # Before rehydrate, a naive snapshot would be empty (the bug). After:
    await d.rehydrate()
    with pytest.raises(GovernanceDenied):
        await d.assert_active("hook_generator_agent", ORG)


async def test_restart_rehydrates_revoked_token_from_db() -> None:
    repo = InMemoryGovernanceRepository()
    a = _instance(repo, InMemoryGovernanceBroadcast())
    await a.start_subscriber()
    contract = MVP_REGISTRY.resolve("hook_generator_agent")
    corr = uuid4()
    token = await a.mint(contract, org_id=ORG, correlation_id=corr)
    await a.revoke(token_id=token.token_id, agent_id=token.agent_id, org_id=ORG,
                   reason="manual", correlation_id=corr)

    # New instance, fresh snapshot, same DB → rehydrate restores the revocation.
    d = _instance(repo, InMemoryGovernanceBroadcast())
    await d.rehydrate()
    allowed = {t.tool_id for t in contract.allowed_tools}
    res = validate_tool_call(
        token=token, public_key=a.public_key,  # same shared signing key
        requested_tool_id="llm.generate", contract_allowed_tool_ids=allowed,
        requested_token_cost=10, tokens_used_so_far=0,
        live_state=d.live_state_checker(ORG),
    )
    assert not res.is_valid
    assert res.failed_stage.value == "revocation"
