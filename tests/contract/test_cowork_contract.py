"""The co-work contract, and the invariant it must not disturb.

Five agents in this codebase are deliberately STATELESS -- memory_read_access=[]
and memory_write_access=[] -- because they judge content, and letting them read
memory their own judgements shaped would close a loop where the system grades its
own homework. The co-work agent is the first contract with genuinely non-empty
memory access, so these tests pin that it neither joins that set nor can reach it.
"""

from __future__ import annotations

import pytest

from skylize.contracts.mvp.cowork import cowork_agent
from skylize.contracts.registry import MVP_REGISTRY, AgentRegistry

#: Must stay outside any feedback loop.
STATELESS_INVARIANT_AGENTS = frozenset(
    {
        "cfo_agent",
        "chief_security_officer",
        "director_ai_safety",
        "llm_safety_agent",
        "prompt_injection_agent",
    }
)


def test_cowork_agent_is_not_one_of_the_stateless_agents() -> None:
    assert cowork_agent.agent_id not in STATELESS_INVARIANT_AGENTS


def test_stateless_agents_still_have_empty_memory_access() -> None:
    """The invariant itself, checked across BOTH registries -- cfo_agent lives in
    the MVP set, the four safety/security judges in the full definitions set."""
    full = AgentRegistry()  # default-loads ALL_DEFINITION_CONTRACTS
    for agent_id in STATELESS_INVARIANT_AGENTS:
        contract = None
        for registry in (MVP_REGISTRY, full):
            try:
                contract = registry.resolve(agent_id)
                break
            except Exception:
                continue
        assert contract is not None, f"{agent_id} is in neither registry"
        assert contract.memory_read_access == [], agent_id
        assert contract.memory_write_access == [], agent_id


def test_cowork_escalation_path_cannot_route_into_a_stateless_agent() -> None:
    """`escalation_path` is the ONE agent-to-agent routing mechanism in this
    codebase (GovernanceAuthority._escalation_path_for resolves it from the
    contract). Naming none of the five is what makes "cannot be routed to them"
    true rather than merely intended."""
    assert not STATELESS_INVARIANT_AGENTS.intersection(cowork_agent.escalation_path)
    assert cowork_agent.escalation_path == ["human_owner"]


def test_cowork_invocable_tools_are_tools_not_agents() -> None:
    """`invocable_tools` routes to TOOLS, so it cannot reach an agent at all --
    but assert the ids are tool ids, not agent ids, so a future edit that pasted
    an agent_id in here would fail loudly."""
    agent_ids = set(MVP_REGISTRY.agent_ids())
    for tool_id in cowork_agent.invocable_tools:
        assert tool_id not in agent_ids
        assert "." in tool_id  # tool ids are namespaced: "llm.generate"


# ---------------------------------------------------------------------------
# The manifest pin -- this test IS the mitigation, not a description of one
# ---------------------------------------------------------------------------

#: Tools a co-work turn may hold while `defers_on_trigger_presence=False`.
#: Both are non-irreversible: `llm.generate` produces text, `memory.search`
#: only reads. That property is the entire reason dropping the request-time
#: LOW_CONFIDENCE_IRREVERSIBLE backstop costs nothing today.
REVERSIBLE_MANIFEST = frozenset({"llm.generate", "memory.search"})


def test_cowork_manifest_is_exactly_the_reversible_pair() -> None:
    """cowork_agent opts out of trigger-PRESENCE deferral at stage 2.5
    (contracts/mvp/cowork.py, `defers_on_trigger_presence=False`), which gives up
    the request-time backstop for LOW_CONFIDENCE_IRREVERSIBLE. The design note
    (docs/architecture/principal_dal_and_hitl_per_turn.md, "what is genuinely
    given up") accepts that cost ONLY because irreversibility is a property of
    TOOLS and this agent holds none that are irreversible.

    So the accepted risk is bounded by this exact set, and nothing else in the
    codebase enforces that bound. Adding `stripe.refund` -- or any side-effecting
    tool -- to the manifest silently converts an accounted-for cost into an
    unaccounted one. This test is what makes that day fail loudly: it must be
    re-argued, and the opt-out re-examined, before the manifest can grow.
    """
    assert {g.tool_id for g in cowork_agent.allowed_tools} == REVERSIBLE_MANIFEST
    # invocable_tools is what is actually offered to the model; a tool could be
    # granted but not offered, so pin both rather than infer one from the other.
    assert set(cowork_agent.invocable_tools) == REVERSIBLE_MANIFEST


def test_cowork_opt_out_is_tied_to_the_pinned_manifest() -> None:
    """The two facts must move together. If someone flips the opt-out back on,
    the manifest bound stops being load-bearing and this test should be revisited
    deliberately rather than left asserting something that no longer matters."""
    assert cowork_agent.defers_on_trigger_presence is False
    assert {g.tool_id for g in cowork_agent.allowed_tools} <= REVERSIBLE_MANIFEST


def test_cowork_agent_is_sandbox() -> None:
    assert cowork_agent.lifecycle_status == "sandbox"


def test_cowork_agent_is_the_only_sandbox_contract() -> None:
    """Everything else stays "active" -- adding the field must not have silently
    reclassified an existing contract."""
    sandboxed = [
        c.agent_id for c in MVP_REGISTRY.all() if c.lifecycle_status == "sandbox"
    ]
    assert sandboxed == ["cowork_agent"]


def test_every_other_contract_defaults_to_active() -> None:
    for contract in MVP_REGISTRY.all():
        if contract.agent_id == "cowork_agent":
            continue
        assert contract.lifecycle_status == "active", contract.agent_id


def test_cowork_agent_has_principal_scoped_memory_access() -> None:
    """Non-empty (it is not a stateless judge) but confined to the principal
    namespace -- never a department or org-wide pattern."""
    assert cowork_agent.memory_read_access == ["principal:*"]
    assert cowork_agent.memory_write_access == ["principal:*"]


def test_cowork_agent_resolves_from_the_wired_registry() -> None:
    """It must be resolvable, or the tool proxy cannot validate a call against
    its contract -- which is what routes chat through the same gate."""
    assert MVP_REGISTRY.resolve("cowork_agent").agent_id == "cowork_agent"


@pytest.mark.parametrize("schema_path", [cowork_agent.input_schema, cowork_agent.output_schema])
def test_cowork_io_schemas_resolve(schema_path: str) -> None:
    from skylize.contracts.registry import resolve_model

    assert resolve_model(schema_path) is not None
