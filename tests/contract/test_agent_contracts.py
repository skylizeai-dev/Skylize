"""
Contract gate — the CI check that fails the build on an inconsistent foundation.

Validates: the MVP registry loads exactly the expected agents with unique ids;
every contract's I/O schema dotted paths resolve to Pydantic models; every
escalation_path ends at human_owner; canonical authority levels only; and the
event registry is internally consistent.
"""

from __future__ import annotations

import pytest

from skylize.app.agents.execution import _AGENT_DELIVERABLE_TYPE
from skylize.contracts.registry import (
    MVP_REGISTRY,
    AgentNotRegistered,
    AgentRegistry,
    resolve_model,
)
from skylize.edge.routes.deliverables import _VALID_TYPES
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
    # seo
    "seo_keyword_agent",
    # finance
    "cfo_agent",
    # sdr
    "sdr_outreach_agent", "lead_qualifier_agent",
    # agency
    "agency_requirements_analyst", "agency_deliverable_drafter",
}


def test_registry_loads_expected_mvp_agents() -> None:
    assert set(MVP_REGISTRY.agent_ids()) == EXPECTED_MVP_AGENTS
    assert len(MVP_REGISTRY.all()) == 21


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


# ---- Deliverable type is declared per agent, never inherited by omission ----

def _agents_typed_by_omission(registry: AgentRegistry) -> list[str]:
    """Registered agents with no `_AGENT_DELIVERABLE_TYPE` entry.

    `execution.py:351` reads `_AGENT_DELIVERABLE_TYPE.get(agent_id, "other")`,
    so such an agent still persists -- silently typed "other", with nothing
    recording that anyone decided that. The type is part of the audit record
    this product sells, so the decision has to be visible in the map.
    """
    return sorted(set(registry.agent_ids()) - set(_AGENT_DELIVERABLE_TYPE))


def test_every_registered_agent_has_an_explicit_deliverable_type() -> None:
    missing = _agents_typed_by_omission(MVP_REGISTRY)
    assert not missing, (
        f"{len(missing)} registered agent(s) have no _AGENT_DELIVERABLE_TYPE "
        f"entry and would be typed 'other' by omission: {missing}. Add an "
        "explicit entry (execution.py) -- 'other' is an acceptable value, but "
        "it must be chosen, not defaulted into."
    )


def test_a_newly_registered_agent_without_an_entry_is_caught() -> None:
    """The gate above must catch a NEW agent, not merely restate that today's 21
    happen to be listed.

    Registering a contract is all it takes to make an agent executable and
    listable (`GET /api/v1/agents`), and `.get(..., "other")` will type it
    without complaint. This builds a registry that is the live one PLUS one new
    contract and asserts the check names it. A separate registry object is used
    deliberately -- mutating MVP_REGISTRY has no clean undo (there is no
    `unregister`), and a leaked probe agent would corrupt every later test.
    """
    probe = MVP_REGISTRY.resolve("hook_generator_agent").model_copy(
        update={"agent_id": "probe_agent_with_no_deliverable_type"}
    )
    with_new_agent = AgentRegistry([*MVP_REGISTRY.all(), probe])

    assert _agents_typed_by_omission(with_new_agent) == [
        "probe_agent_with_no_deliverable_type"
    ]
    # ...and the live registry is untouched by the probe.
    assert "probe_agent_with_no_deliverable_type" not in MVP_REGISTRY.agent_ids()


def test_deliverable_type_map_has_no_entry_for_an_unregistered_agent() -> None:
    """The other direction: a stale entry for an agent that no longer exists is
    dead configuration that makes the map look more complete than it is."""
    stale = sorted(set(_AGENT_DELIVERABLE_TYPE) - set(MVP_REGISTRY.agent_ids()))
    assert not stale, f"_AGENT_DELIVERABLE_TYPE entries for unregistered agents: {stale}"


def test_every_deliverable_type_is_in_the_persisted_vocabulary() -> None:
    """Every mapped value must satisfy the CHECK constraint on
    `deliverables.deliverable_type` (migration 0006:42-47). A value outside it
    is not a mistyped deliverable -- it is an INSERT that fails at the database,
    after the model has already been called and billed."""
    bad = {
        agent_id: value
        for agent_id, value in _AGENT_DELIVERABLE_TYPE.items()
        if value not in _VALID_TYPES
    }
    assert not bad, f"deliverable types outside the persisted vocabulary: {bad}"


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
