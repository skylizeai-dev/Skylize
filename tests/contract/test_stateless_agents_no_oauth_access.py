"""The stateless-agent invariant, asserted against the OAuth infrastructure.

Five agents are stateless by contract — `memory_read_access` and
`memory_write_access` are both empty — and this pass must not give any of them a
path to the new `oauth_credentials` table.

The invariant is enforced STRUCTURALLY rather than by convention. A contract can
only reach that table by invoking a tool that declares a `ToolOAuthProfile`, so
the test asserts both halves: the five contracts stay stateless, and none of
their grantable/invocable tools carries an oauth profile.

Written as an executable test rather than a claim in a report so a future pass
that hands one of these agents a Drive tool fails here instead of shipping.
"""

from __future__ import annotations

import pytest

from skylize.contracts.mvp.finance import cfo_agent
from skylize.contracts.mvp.safety import ALL_SAFETY_CONTRACTS
from skylize.tools.builtin import default_tool_registry

STATELESS_AGENT_IDS = {
    "cfo_agent",
    "chief_security_officer",
    "director_ai_safety",
    "llm_safety_agent",
    "prompt_injection_agent",
}


def _stateless_contracts():
    by_id = {c.agent_id: c for c in [*ALL_SAFETY_CONTRACTS, cfo_agent]}
    missing = STATELESS_AGENT_IDS - by_id.keys()
    assert not missing, f"stateless contracts not found: {sorted(missing)}"
    return [by_id[a] for a in sorted(STATELESS_AGENT_IDS)]


@pytest.mark.parametrize(
    "contract", _stateless_contracts(), ids=lambda c: c.agent_id
)
def test_stateless_agents_have_no_memory_access(contract) -> None:
    """Unchanged by this pass — the OAuth work touches no memory path at all."""
    assert contract.memory_read_access == [], (
        f"{contract.agent_id} gained memory read access"
    )
    assert contract.memory_write_access == [], (
        f"{contract.agent_id} gained memory write access"
    )


@pytest.mark.parametrize(
    "contract", _stateless_contracts(), ids=lambda c: c.agent_id
)
def test_stateless_agents_hold_no_oauth_capable_tool(contract) -> None:
    """No stateless agent may hold a tool that reaches an OAuth grant.

    `ToolProxy._ensure_oauth_credential` runs only for a tool whose definition
    declares `oauth`, so a contract granting no such tool cannot reach
    `oauth_credentials` by any route through the proxy.
    """
    registry = default_tool_registry()
    granted = {g.tool_id for g in contract.allowed_tools}
    granted |= set(contract.invocable_tools or [])

    for tool_id in granted:
        try:
            tool = registry.resolve(tool_id)
        except Exception:
            # Not a registered runtime tool (e.g. `llm.generate`, handled by the
            # gateway). Nothing registered means nothing with an oauth profile.
            continue
        assert tool.oauth is None, (
            f"{contract.agent_id} may invoke {tool_id!r}, which declares an "
            f"OAuth profile ({tool.oauth}) — this breaks the stateless-agent "
            f"invariant for the oauth_credentials table"
        )


def test_no_builtin_tool_declares_an_oauth_profile_yet() -> None:
    """Guards the assumption the test above leans on.

    This pass ships provider-agnostic INFRASTRUCTURE and no connector, so no
    builtin tool should declare an oauth profile. When the first connector lands
    this test must be updated deliberately — and that edit is the moment to
    re-check which contracts may hold the new tool.
    """
    registry = default_tool_registry()
    with_oauth = [t.tool_id for t in registry.all() if t.oauth is not None]
    assert with_oauth == [], (
        f"tools now declare an OAuth profile: {with_oauth}. Re-verify the "
        "stateless-agent invariant before relaxing this assertion."
    )
