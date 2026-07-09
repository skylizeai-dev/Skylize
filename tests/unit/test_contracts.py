"""
Unit tests for the AgentContract registry.

Covers (per spec):
  1. resolve() returns known contract
  2. resolve() raises AgentNotRegistered for unknown agent_id (fail closed)
  3. frozen contracts cannot be mutated
  4. register_contract() adds a new contract resolvable immediately
  5. AgentRegistry() default-loads definitions/ contracts (cfo, etc.)
"""

from __future__ import annotations

import pytest

from skylize.contracts import (
    AgentContract,
    AgentNotRegistered,
    AgentRegistry,
    FailureMode,
    HumanInLoopTrigger,
    ToolGrant,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def registry() -> AgentRegistry:
    return AgentRegistry()


# ---------------------------------------------------------------------------
# 1. Resolve a known agent
# ---------------------------------------------------------------------------


def test_resolve_cfo(registry: AgentRegistry) -> None:
    contract = registry.resolve("cfo")
    assert contract.agent_id == "cfo"
    assert contract.authority_level == "executive"
    assert contract.department == "finance"
    assert contract.escalation_path == ["human_owner"]
    assert contract.failure_mode == FailureMode.ESCALATE_IMMEDIATELY
    assert contract.governance_token_required is True


def test_resolve_fraud_detection_agent(registry: AgentRegistry) -> None:
    contract = registry.resolve("fraud_detection_agent")
    assert contract.agent_id == "fraud_detection_agent"
    assert contract.authority_level == "worker"
    assert contract.department == "security"
    assert contract.failure_mode == FailureMode.FAIL_CLOSED
    assert HumanInLoopTrigger.SECURITY_SEVERITY_HIGH in contract.human_in_loop_triggers


def test_resolve_hook_generator_agent(registry: AgentRegistry) -> None:
    contract = registry.resolve("hook_generator_agent")
    assert contract.agent_id == "hook_generator_agent"
    assert contract.authority_level == "worker"
    assert contract.department == "creative"
    assert contract.failure_mode == FailureMode.FALLBACK_DEGRADED
    assert contract.memory_write_access == []


def test_resolve_vp_finance(registry: AgentRegistry) -> None:
    contract = registry.resolve("vp_finance")
    assert contract.escalation_path == ["cfo", "human_owner"]
    assert contract.max_token_budget == 80_000
    assert contract.max_execution_time_seconds == 420


def test_resolve_chief_security_officer(registry: AgentRegistry) -> None:
    contract = registry.resolve("chief_security_officer")
    assert contract.authority_level == "executive"
    assert contract.failure_mode == FailureMode.FAIL_CLOSED
    assert contract.escalation_path == ["ceo", "human_owner"]


def test_resolve_director_ai_safety(registry: AgentRegistry) -> None:
    contract = registry.resolve("director_ai_safety")
    assert contract.authority_level == "director"
    assert "chief_security_officer" in contract.escalation_path
    assert "human_owner" in contract.escalation_path


def test_resolve_tone_of_voice_agent(registry: AgentRegistry) -> None:
    contract = registry.resolve("tone_of_voice_agent")
    assert contract.failure_mode == FailureMode.FAIL_CLOSED
    assert contract.memory_write_access == []
    assert contract.human_in_loop_triggers == []


# ---------------------------------------------------------------------------
# 2. Raises AgentNotRegistered for unknown agent_id (fail closed)
# ---------------------------------------------------------------------------


def test_unknown_agent_raises(registry: AgentRegistry) -> None:
    with pytest.raises(AgentNotRegistered):
        registry.resolve("nonexistent_agent_xyz")


def test_unknown_agent_message_contains_id(registry: AgentRegistry) -> None:
    with pytest.raises(AgentNotRegistered, match="bad_agent_id"):
        registry.resolve("bad_agent_id")


def test_empty_string_raises(registry: AgentRegistry) -> None:
    with pytest.raises(AgentNotRegistered):
        registry.resolve("")


# ---------------------------------------------------------------------------
# 3. Frozen contracts cannot be mutated
# ---------------------------------------------------------------------------


def test_contract_is_frozen(registry: AgentRegistry) -> None:
    contract = registry.resolve("cfo")
    with pytest.raises(Exception):
        contract.agent_id = "hacked"  # type: ignore[misc]


def test_contract_allowed_tools_tuple_immutable(registry: AgentRegistry) -> None:
    contract = registry.resolve("cfo")
    with pytest.raises(Exception):
        contract.allowed_tools = []  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 4. register_contract() adds a contract that is immediately resolvable
# ---------------------------------------------------------------------------


def test_register_and_resolve_custom_contract() -> None:
    reg = AgentRegistry()
    custom = AgentContract(
        agent_id="test_custom_agent",
        agent_role="Test Agent",
        authority_level="worker",
        department="test",
        input_schema="skylize.schemas.creative.HookRequestIn",
        output_schema="skylize.schemas.creative.HooksOut",
        allowed_tools=[
            ToolGrant(tool_id="llm.generate", purpose="test", max_calls_per_run=1),
        ],
        max_token_budget=1_000,
        max_execution_time_seconds=30,
        escalation_path=["human_owner"],
        failure_mode=FailureMode.FAIL_CLOSED,
        memory_read_access=[],
        memory_write_access=[],
    )
    reg.register_contract(custom)
    resolved = reg.resolve("test_custom_agent")
    assert resolved.agent_id == "test_custom_agent"
    assert resolved.max_token_budget == 1_000


def test_register_contract_replaces_existing() -> None:
    reg = AgentRegistry()
    original = reg.resolve("cfo")
    replacement = AgentContract(
        **{
            **original.model_dump(),
            "max_token_budget": 999,
        }
    )
    reg.register_contract(replacement)
    resolved = reg.resolve("cfo")
    assert resolved.max_token_budget == 999


# ---------------------------------------------------------------------------
# 5. Default load covers all expected definitions/ contracts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_id", [
    "cfo",
    "vp_finance",
    "director_capital_allocation",
    "director_fpanda",
    "director_risk",
    "director_treasury",
    "chief_security_officer",
    "director_ai_safety",
    "llm_safety_agent",
    "prompt_injection_agent",
    "fraud_detection_agent",
    "hook_generator_agent",
    "ad_copy_agent",
    "caption_writer_agent",
    "script_writer_agent",
    "cta_optimizer_agent",
    "tone_of_voice_agent",
])
def test_all_definition_contracts_resolvable(
    registry: AgentRegistry, agent_id: str
) -> None:
    contract = registry.resolve(agent_id)
    assert contract.agent_id == agent_id


# ---------------------------------------------------------------------------
# 6. Invariants: escalation_path always ends at human_owner
# ---------------------------------------------------------------------------


def test_all_escalation_paths_end_at_human_owner(registry: AgentRegistry) -> None:
    for contract in registry.all():
        assert contract.escalation_path[-1] == "human_owner", (
            f"{contract.agent_id}: escalation_path must end at 'human_owner', "
            f"got {contract.escalation_path}"
        )


# ---------------------------------------------------------------------------
# 7. ToolGrant is also frozen
# ---------------------------------------------------------------------------


def test_tool_grant_is_frozen() -> None:
    grant = ToolGrant(tool_id="llm.generate", purpose="test")
    with pytest.raises(Exception):
        grant.tool_id = "hacked"  # type: ignore[misc]
