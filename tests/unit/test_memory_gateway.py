"""Unit tests for MemoryGateway contract-level permission enforcement."""

from __future__ import annotations


import pytest

from skylize.contracts.base import AgentContract, FailureMode, ToolGrant
from skylize.contracts.registry import MVP_REGISTRY, AgentRegistry
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


def _scope(org_id: str = "org-1", department: str | None = None) -> MemoryScope:
    return MemoryScope(org_id=org_id, department=department)


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
@pytest.mark.skip(reason="chief_security_officer contract not in MVP registry; memory gateway is unwired from bootstrap (dead code, no tracked rework plan)")
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


# ---------------------------------------------------------------------------
# Tests: per-namespace scope match (0.6 audit gap — non-emptiness is not enough)
# ---------------------------------------------------------------------------

#: A narrow cross-department grant, in contract form. `director_risk` used to
#: supply this shape from contracts/definitions/, which is deleted; the
#: behaviour under test belongs to the GATEWAY's namespace matcher, not to any
#: particular agent, so the grant is stated here directly.
_CROSS_DEPT_READER = AgentContract(
    agent_id="cross_dept_reader",
    agent_role="Cross-department reader - narrow grant into another department",
    authority_level="director",
    department="finance",
    input_schema="skylize.schemas.agents.finance.BudgetSummaryExecuteIn",
    output_schema="skylize.schemas.agents.finance.BudgetSummaryExecuteOut",
    allowed_tools=[ToolGrant(tool_id="memory.search", purpose="recall")],
    max_token_budget=10_000,
    max_execution_time_seconds=60,
    escalation_path=["human_owner"],
    failure_mode=FailureMode.FAIL_CLOSED,
    memory_read_access=["finance:risk:*", "security:fraud:summary"],
    memory_write_access=["finance:risk:assessments"],
)


@pytest.fixture()
def grant_registry() -> AgentRegistry:
    return AgentRegistry([*MVP_REGISTRY.all(), _CROSS_DEPT_READER])


@pytest.fixture()
def definitions_gateway(adapter: _FakeAdapter, grant_registry: AgentRegistry) -> MemoryGateway:
    return MemoryGateway(adapter=adapter, registry=grant_registry)


@pytest.mark.asyncio
async def test_read_outside_granted_namespaces_denied(definitions_gateway: MemoryGateway) -> None:
    """fraud_detection_agent is granted security:fraud:* and security:patterns only."""
    scope = _scope(department="security:incidents")
    with pytest.raises(MemoryPermissionDenied, match="fraud_detection_agent"):
        await definitions_gateway.read("fraud_detection_agent", scope, caller_org_id="org-1")


@pytest.mark.asyncio
async def test_write_outside_granted_namespaces_denied(definitions_gateway: MemoryGateway) -> None:
    """fraud_detection_agent may only write security:fraud:signals, not security:fraud:raw."""
    scope = _scope(department="security:fraud:raw")
    with pytest.raises(MemoryPermissionDenied, match="fraud_detection_agent"):
        await definitions_gateway.write(
            "fraud_detection_agent", scope, _entry(), caller_org_id="org-1"
        )


@pytest.mark.asyncio
async def test_wildcard_grant_covers_sub_namespace(
    definitions_gateway: MemoryGateway, adapter: _FakeAdapter
) -> None:
    """security:fraud:* covers security:fraud:signals (read)."""
    scope = _scope(department="security:fraud:signals")
    result = await definitions_gateway.read("fraud_detection_agent", scope, caller_org_id="org-1")
    assert result == []


@pytest.mark.asyncio
async def test_exact_grant_matches_only_exactly(definitions_gateway: MemoryGateway) -> None:
    """security:patterns is an exact grant — a sub-namespace of it is NOT covered."""
    scope = _scope(department="security:patterns:extra")
    with pytest.raises(MemoryPermissionDenied, match="fraud_detection_agent"):
        await definitions_gateway.read("fraud_detection_agent", scope, caller_org_id="org-1")


@pytest.mark.asyncio
async def test_exact_grant_matches_exact_namespace(
    definitions_gateway: MemoryGateway, adapter: _FakeAdapter
) -> None:
    scope = _scope(department="security:patterns")
    result = await definitions_gateway.read("fraud_detection_agent", scope, caller_org_id="org-1")
    assert result == []


@pytest.mark.asyncio
async def test_cross_department_read_allowed(
    definitions_gateway: MemoryGateway, adapter: _FakeAdapter
) -> None:
    """The contract explicitly grants the cross-dept read security:fraud:summary."""
    scope = _scope(department="security:fraud:summary")
    result = await definitions_gateway.read("cross_dept_reader", scope, caller_org_id="org-1")
    assert result == []


@pytest.mark.asyncio
async def test_cross_department_grant_does_not_open_the_department(
    definitions_gateway: MemoryGateway,
) -> None:
    """The cross-dept grant is narrow — it does not open all of security:*."""
    scope = _scope(department="security:fraud:raw")
    with pytest.raises(MemoryPermissionDenied, match="cross_dept_reader"):
        await definitions_gateway.read("cross_dept_reader", scope, caller_org_id="org-1")


@pytest.mark.asyncio
async def test_zero_scope_agent_still_denied_regardless_of_namespace(
    definitions_gateway: MemoryGateway,
) -> None:
    """Empty memory_read_access must still deny outright, whatever namespace is asked for."""
    scope = _scope(department="security:fraud:summary")
    with pytest.raises(MemoryPermissionDenied, match="cfo_agent"):
        await definitions_gateway.read("cfo_agent", scope, caller_org_id="org-1")
