"""Unit tests for AgentPromptService."""

from __future__ import annotations

import pytest

from skylize.app.agent_prompts.service import AgentPromptService
from skylize.contracts import AgentRegistry
from skylize.contracts.registry import AgentNotRegistered


@pytest.fixture(scope="module")
def registry() -> AgentRegistry:
    return AgentRegistry()


@pytest.fixture(scope="module")
def svc(registry: AgentRegistry) -> AgentPromptService:
    return AgentPromptService(registry)


# ---------------------------------------------------------------------------
# test_known_agent_returns_prompt
# ---------------------------------------------------------------------------


def test_known_agent_returns_prompt(svc: AgentPromptService) -> None:
    resp = svc.get_prompt("cfo", org_id="test-org")
    assert resp.agent_id == "cfo"
    assert resp.department == "finance"
    assert resp.system_prompt  # non-empty
    assert "Chief Financial Officer" in resp.system_prompt


# ---------------------------------------------------------------------------
# test_unknown_agent_raises_404
# ---------------------------------------------------------------------------


def test_unknown_agent_raises(svc: AgentPromptService) -> None:
    with pytest.raises(AgentNotRegistered):
        svc.get_prompt("nonexistent_xyz", org_id="test-org")


# ---------------------------------------------------------------------------
# test_frontier_model_for_executive
# ---------------------------------------------------------------------------


def test_frontier_model_for_executive(svc: AgentPromptService) -> None:
    resp = svc.get_prompt("cfo", org_id="test-org")
    assert resp.authority_level == "executive"
    assert resp.model_tier == "frontier"


def test_frontier_model_for_vp(svc: AgentPromptService) -> None:
    resp = svc.get_prompt("vp_finance", org_id="test-org")
    assert resp.authority_level == "vp"
    assert resp.model_tier == "frontier"


# ---------------------------------------------------------------------------
# test_mini_model_for_worker
# ---------------------------------------------------------------------------


def test_mini_model_for_worker(svc: AgentPromptService) -> None:
    resp = svc.get_prompt("hook_generator_agent", org_id="test-org")
    assert resp.authority_level == "worker"
    assert resp.model_tier == "mini"


def test_mini_model_for_director(svc: AgentPromptService) -> None:
    resp = svc.get_prompt("director_capital_allocation", org_id="test-org")
    assert resp.authority_level == "director"
    assert resp.model_tier == "mini"


# ---------------------------------------------------------------------------
# Response fields are correctly populated
# ---------------------------------------------------------------------------


def test_response_contains_failure_mode(svc: AgentPromptService) -> None:
    resp = svc.get_prompt("cfo", org_id="test-org")
    assert resp.failure_mode == "escalate_immediately"


def test_response_memory_read_access(svc: AgentPromptService) -> None:
    resp = svc.get_prompt("cfo", org_id="test-org")
    assert "finance:*" in resp.memory_read_access


def test_response_human_in_loop_triggers(svc: AgentPromptService) -> None:
    resp = svc.get_prompt("cfo", org_id="test-org")
    assert "spend_over_ceiling" in resp.human_in_loop_triggers


def test_response_max_token_budget(svc: AgentPromptService) -> None:
    resp = svc.get_prompt("cfo", org_id="test-org")
    assert resp.max_token_budget == 120_000
