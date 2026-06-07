"""
Contract gate — the CI check that fails the build on an inconsistent foundation.

Validates: the MVP registry loads exactly the expected agents with unique ids;
every contract's I/O schema dotted paths resolve to Pydantic models; every
escalation_path ends at human_owner; canonical authority levels only; and the
event registry is internally consistent.
"""

from __future__ import annotations

import pytest

from skylize.contracts.registry import (
    MVP_REGISTRY,
    AgentNotRegistered,
    resolve_model,
)
from skylize.schemas.events import EVENT_REGISTRY

_CANONICAL_LEVELS = {"executive", "vp", "director", "manager", "worker"}

EXPECTED_MVP_AGENTS = {
    # executive
    "ceo", "cmo",
    # creative
    "vp_creative", "copy_director", "art_director", "creative_operations_manager",
    "hook_generator_agent", "ad_copy_agent", "caption_writer_agent",
    "script_writer_agent", "cta_optimizer_agent",
    # brand
    "brand_guardian_agent", "tone_of_voice_agent",
    # growth
    "director_growth",
    # security
    "fraud_detection_agent",
}


def test_registry_loads_expected_mvp_agents() -> None:
    assert set(MVP_REGISTRY.agent_ids()) == EXPECTED_MVP_AGENTS
    assert len(MVP_REGISTRY.all()) == 15


def test_agent_ids_are_unique_and_snake_case() -> None:
    ids = MVP_REGISTRY.agent_ids()
    assert len(ids) == len(set(ids))
    for agent_id in ids:
        assert agent_id == agent_id.lower()
        assert " " not in agent_id


def test_all_io_schema_paths_resolve() -> None:
    # This is the heart of the contract gate.
    MVP_REGISTRY.validate_schemas()
    # And spot-check that resolution truly returns a Pydantic model.
    model = resolve_model("skylize.schemas.agents.creative.HooksOut")
    assert model.__name__ == "HooksOut"


def test_authority_levels_are_canonical() -> None:
    for contract in MVP_REGISTRY.all():
        assert contract.authority_level in _CANONICAL_LEVELS


def test_escalation_paths_end_at_human_owner() -> None:
    for contract in MVP_REGISTRY.all():
        assert contract.escalation_path[-1] == "human_owner", contract.agent_id


def test_non_persisting_workers_have_empty_write_access() -> None:
    # hook_generator is the canonical "propose, don't persist" worker.
    hook = MVP_REGISTRY.resolve("hook_generator_agent")
    assert hook.memory_write_access == []
    assert hook.authority_level == "worker"


def test_security_worker_fails_closed() -> None:
    fraud = MVP_REGISTRY.resolve("fraud_detection_agent")
    assert fraud.failure_mode.value == "fail_closed"


def test_unknown_agent_fails_closed() -> None:
    with pytest.raises(AgentNotRegistered):
        MVP_REGISTRY.resolve("does_not_exist")


def test_tenant_override_only_tightens_budget() -> None:
    base = MVP_REGISTRY.resolve("hook_generator_agent")
    tightened = MVP_REGISTRY.resolve(
        "hook_generator_agent",
        tenant_overrides={"max_token_budget": base.max_token_budget - 1000},
    )
    assert tightened.max_token_budget == base.max_token_budget - 1000
    # An attempt to loosen is clamped to the platform contract.
    not_loosened = MVP_REGISTRY.resolve(
        "hook_generator_agent",
        tenant_overrides={"max_token_budget": base.max_token_budget + 999_999},
    )
    assert not_loosened.max_token_budget == base.max_token_budget


# ---- Event registry consistency ----

def test_event_registry_keys_match_type_defaults() -> None:
    for type_str, cls in EVENT_REGISTRY.items():
        assert cls.model_fields["type"].default == type_str


def test_event_registry_has_all_six_categories() -> None:
    categories = {cls.model_fields["category"].default.value for cls in EVENT_REGISTRY.values()}
    assert categories == {"creative", "sales", "memory", "decision", "governance", "audit"}
