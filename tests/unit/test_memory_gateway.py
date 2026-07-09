"""Unit tests for MemoryGateway contract-level permission enforcement."""

from __future__ import annotations


import pytest

from skylize.contracts.registry import AgentRegistry
from skylize.memory.exceptions import MemoryNamespaceViolation, MemoryPermissionDenied
from skylize.memory.gateway import MemoryGateway
from skylize.schemas.memory import MemoryEntry, MemoryScope


# ---------------------------------------------------------------------------
# Minimal stub adapter
# ---------------------------------------------------------------------------

class _FakeAdapter:
    def __init__(self) -> None:
        self.stored: list[tuple[MemoryScope, MemoryEntry]] = []

    async def retrieve(self, scope: MemoryScope) -> list[MemoryEntry]:
        return []

    async def store(self, scope: MemoryScope, entry: MemoryEntry) -> None:
        self.stored.append((scope, entry))

    async def is_stateless(self, agent_id: str) -> bool:
        return False


def _entry(org_id: str = "org-1", importance_score: float = 1.0) -> MemoryEntry:
    return MemoryEntry(
        org_id=org_id,
        agent_id="test_agent",
        scope="default",
        tier="episodic",
        content_text="hello",
        created_by_agent="test_agent",
        importance_score=importance_score,
    )


def _scope(org_id: str = "org-1") -> MemoryScope:
    return MemoryScope(org_id=org_id)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def registry() -> AgentRegistry:
    from skylize.contracts.registry import MVP_REGISTRY
    return MVP_REGISTRY


@pytest.fixture()
def adapter() -> _FakeAdapter:
    return _FakeAdapter()


@pytest.fixture()
def gateway(adapter: _FakeAdapter, registry: AgentRegistry) -> MemoryGateway:
    return MemoryGateway(adapter=adapter, registry=registry)


# ---------------------------------------------------------------------------
# Tests: read permission
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cfo_read_raises_permission_denied(gateway: MemoryGateway) -> None:
    """CFO has memory_read_access=[] — any read attempt must raise."""
    with pytest.raises(MemoryPermissionDenied, match="cfo_agent"):
        await gateway.read("cfo_agent", _scope(), caller_org_id="org-1")


@pytest.mark.asyncio
@pytest.mark.skip(reason="chief_security_officer contract not in MVP registry; memory gateway is unwired from bootstrap - M5 rework per launch plan")
async def test_safety_agents_read_raises_permission_denied(gateway: MemoryGateway) -> None:
    for agent_id in (
        "chief_security_officer",
        "director_ai_safety",
        "llm_safety_agent",
        "prompt_injection_agent",
    ):
        with pytest.raises(MemoryPermissionDenied):
            await gateway.read(agent_id, _scope(), caller_org_id="org-1")


@pytest.mark.asyncio
async def test_stateful_agent_read_succeeds(gateway: MemoryGateway) -> None:
    """CEO has memory_read_access — read should succeed (returns empty list from stub)."""
    result = await gateway.read("ceo", _scope(), caller_org_id="org-1")
    assert result == []


# ---------------------------------------------------------------------------
# Tests: write permission
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cfo_write_raises_permission_denied(gateway: MemoryGateway) -> None:
    with pytest.raises(MemoryPermissionDenied, match="cfo_agent"):
        await gateway.write("cfo_agent", _scope(), _entry(), caller_org_id="org-1")


# ---------------------------------------------------------------------------
# Tests: namespace guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mismatched_org_id_read_raises_namespace_violation(gateway: MemoryGateway) -> None:
    scope = _scope(org_id="org-ATTACKER")
    with pytest.raises(MemoryNamespaceViolation):
        await gateway.read("ceo", scope, caller_org_id="org-VICTIM")


@pytest.mark.asyncio
async def test_mismatched_org_id_write_raises_namespace_violation(gateway: MemoryGateway) -> None:
    scope = _scope(org_id="org-ATTACKER")
    with pytest.raises(MemoryNamespaceViolation):
        await gateway.write("ceo", scope, _entry(), caller_org_id="org-VICTIM")


# ---------------------------------------------------------------------------
# Tests: importance score filter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_low_importance_score_skips_write(
    gateway: MemoryGateway, adapter: _FakeAdapter
) -> None:
    """importance_score=0.35 < 0.40 threshold — must skip without error."""
    low_entry = _entry(importance_score=0.35)
    await gateway.write("ceo", _scope(), low_entry, caller_org_id="org-1")
    assert len(adapter.stored) == 0


@pytest.mark.asyncio
async def test_importance_score_at_threshold_writes(
    gateway: MemoryGateway, adapter: _FakeAdapter
) -> None:
    """importance_score=0.40 exactly meets threshold — must persist."""
    entry = _entry(importance_score=0.40)
    await gateway.write("ceo", _scope(), entry, caller_org_id="org-1")
    assert len(adapter.stored) == 1


@pytest.mark.asyncio
async def test_high_importance_score_writes(
    gateway: MemoryGateway, adapter: _FakeAdapter
) -> None:
    entry = _entry(importance_score=0.90)
    await gateway.write("ceo", _scope(), entry, caller_org_id="org-1")
    assert len(adapter.stored) == 1
