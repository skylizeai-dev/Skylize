"""Unit tests for AgentRegistrySync against the InMemoryContractRepository fake."""

from __future__ import annotations

import pytest

from skylize.app.agent_service import AgentRegistrySync
from skylize.contracts.mvp.sdr import lead_qualifier_agent, sdr_outreach_agent
from skylize.contracts.registry import AgentRegistry
from skylize.dal.memory import InMemoryContractRepository

_TEST_CONTRACTS = [sdr_outreach_agent, lead_qualifier_agent]
_TEST_REGISTRY = AgentRegistry(contracts=_TEST_CONTRACTS)


@pytest.fixture()
def repo() -> InMemoryContractRepository:
    return InMemoryContractRepository()


@pytest.fixture()
def sync(repo: InMemoryContractRepository) -> AgentRegistrySync:
    return AgentRegistrySync(contract_repo=repo, in_memory_registry=_TEST_REGISTRY)


# ---------------------------------------------------------------------------
# sync_to_db
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_to_db_returns_count(sync: AgentRegistrySync) -> None:
    count = await sync.sync_to_db()
    assert count == 2


@pytest.mark.asyncio
async def test_sync_to_db_persists_both_agents(
    sync: AgentRegistrySync, repo: InMemoryContractRepository
) -> None:
    await sync.sync_to_db()
    rows = await repo.load_all_active()
    persisted_ids = {agent_id for agent_id, _ in rows}
    assert "sdr_outreach_agent" in persisted_ids
    assert "lead_qualifier_agent" in persisted_ids


@pytest.mark.asyncio
async def test_sync_to_db_is_idempotent(
    sync: AgentRegistrySync, repo: InMemoryContractRepository
) -> None:
    await sync.sync_to_db()
    await sync.sync_to_db()
    rows = await repo.load_all_active()
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# load_from_db
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_from_db_returns_registry(sync: AgentRegistrySync) -> None:
    await sync.sync_to_db()
    registry = await sync.load_from_db()
    assert isinstance(registry, AgentRegistry)


@pytest.mark.asyncio
async def test_load_from_db_contains_synced_agents(sync: AgentRegistrySync) -> None:
    await sync.sync_to_db()
    registry = await sync.load_from_db()
    assert "sdr_outreach_agent" in registry.agent_ids()
    assert "lead_qualifier_agent" in registry.agent_ids()


@pytest.mark.asyncio
async def test_load_from_db_reconstructs_contract_faithfully(sync: AgentRegistrySync) -> None:
    await sync.sync_to_db()
    registry = await sync.load_from_db()
    reloaded = registry.resolve("sdr_outreach_agent")
    assert reloaded.agent_id == sdr_outreach_agent.agent_id
    assert reloaded.authority_level == sdr_outreach_agent.authority_level
    assert reloaded.max_token_budget == sdr_outreach_agent.max_token_budget
    assert reloaded.governance_token_required == sdr_outreach_agent.governance_token_required


@pytest.mark.asyncio
async def test_load_from_db_invalid_json_raises(
    sync: AgentRegistrySync, repo: InMemoryContractRepository
) -> None:
    # Poison the repo with a row that won't validate as AgentContract
    await repo.upsert("bad_agent", 1, '{"not": "a valid contract"}')
    with pytest.raises((ValueError, Exception)):
        await sync.load_from_db()


# ---------------------------------------------------------------------------
# get_contract_db
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_contract_db_found(sync: AgentRegistrySync) -> None:
    await sync.sync_to_db()
    result = await sync.get_contract_db("sdr_outreach_agent")
    assert result is not None
    assert result.agent_id == "sdr_outreach_agent"


@pytest.mark.asyncio
async def test_get_contract_db_not_found(sync: AgentRegistrySync) -> None:
    await sync.sync_to_db()
    result = await sync.get_contract_db("nonexistent_agent")
    assert result is None


@pytest.mark.asyncio
async def test_get_contract_db_preserves_human_in_loop_triggers(sync: AgentRegistrySync) -> None:
    await sync.sync_to_db()
    result = await sync.get_contract_db("sdr_outreach_agent")
    assert result is not None
    trigger_values = [t.value for t in result.human_in_loop_triggers]
    assert "first_external_launch" in trigger_values
