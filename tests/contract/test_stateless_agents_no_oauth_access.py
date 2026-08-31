"""The stateless-agent invariant, asserted against the OAuth + Drive surfaces.

Five agents are stateless by contract — `memory_read_access` and
`memory_write_access` are both empty — and no pass may give any of them a path to
`oauth_credentials`, `org_permission_grants`, or a Drive tool.

The invariant is enforced STRUCTURALLY rather than by convention. A contract can
only reach those tables by invoking a tool that declares a `ToolOAuthProfile` or a
`ToolPermissionProfile`, so the tests assert both halves: the five contracts stay
stateless, and none of their grantable/invocable tools carries either profile.

IMPORTANT — the registry under test is the FULLY WIRED one. An earlier version of
this file called `default_tool_registry()` with no arguments, which silently omits
every credential-backed tool (HubSpot, Drive) because their builders are skipped
when no vault/OAuth service is passed. That version would have passed even after
the Drive tools landed, proving nothing about them. `_full_registry` below wires
the same integrations bootstrap does, so the assertions cover what production
actually registers.
"""

from __future__ import annotations

import pytest

from skylize.app.audit.service import AuditService
from skylize.app.credentials.encryption import FernetEncryptor
from skylize.app.credentials.oauth import OAuthCredentialService
from skylize.app.credentials.vault import CredentialVault
from skylize.contracts.mvp.finance import cfo_agent
from skylize.contracts.mvp.safety import ALL_SAFETY_CONTRACTS
from skylize.dal.credentials import InMemoryCredentialRepository
from skylize.dal.memory import InMemoryAuditRepository
from skylize.dal.oauth_credentials import InMemoryOAuthCredentialRepository
from skylize.events.memory_bus import InMemoryEventBus
from skylize.tools.builtin import default_tool_registry
from skylize.tools.registry import ToolRegistry

STATELESS_AGENT_IDS = {
    "cfo_agent",
    "chief_security_officer",
    "director_ai_safety",
    "llm_safety_agent",
    "prompt_injection_agent",
}

#: Tools that reach a customer's third-party account. Named explicitly so a new
#: connector has to be added here consciously.
EXPECTED_OAUTH_TOOL_IDS = {
    "integration.drive_create_file",
    "integration.drive_share_file",
}

#: Tools performing an elevated action gated by the org allow-list.
EXPECTED_PERMISSION_TOOL_IDS = {"integration.drive_share_file"}

TEST_KEY = "c2t5bGl6ZS1pbnRlZ3JhdGlvbi10ZXN0LWtleSF4MzI="


def _full_registry() -> ToolRegistry:
    """The registry as bootstrap wires it — every credential-backed tool present."""
    audit = AuditService(InMemoryEventBus(), InMemoryAuditRepository())
    encryptor = FernetEncryptor(TEST_KEY)
    vault = CredentialVault(encryptor, InMemoryCredentialRepository(), audit)
    oauth = OAuthCredentialService(
        encryptor=encryptor,
        repo=InMemoryOAuthCredentialRepository(),
        audit=audit,
    )
    return default_tool_registry(credential_vault=vault, oauth_credentials=oauth)


def _stateless_contracts():
    by_id = {c.agent_id: c for c in [*ALL_SAFETY_CONTRACTS, cfo_agent]}
    missing = STATELESS_AGENT_IDS - by_id.keys()
    assert not missing, f"stateless contracts not found: {sorted(missing)}"
    return [by_id[a] for a in sorted(STATELESS_AGENT_IDS)]


@pytest.mark.parametrize("contract", _stateless_contracts(), ids=lambda c: c.agent_id)
def test_stateless_agents_have_no_memory_access(contract) -> None:
    assert contract.memory_read_access == [], (
        f"{contract.agent_id} gained memory read access"
    )
    assert contract.memory_write_access == [], (
        f"{contract.agent_id} gained memory write access"
    )


@pytest.mark.parametrize("contract", _stateless_contracts(), ids=lambda c: c.agent_id)
def test_stateless_agents_hold_no_oauth_capable_tool(contract) -> None:
    """No stateless agent may hold a tool that reaches an OAuth grant.

    `ToolProxy._ensure_oauth_credential` runs only for a tool declaring `oauth`,
    so a contract granting no such tool cannot reach `oauth_credentials`.
    """
    registry = _full_registry()
    granted = {g.tool_id for g in contract.allowed_tools}
    granted |= set(contract.invocable_tools or [])

    for tool_id in granted:
        if not registry.has(tool_id):
            continue  # not a registered runtime tool (e.g. llm.generate)
        tool = registry.resolve(tool_id)
        assert tool.oauth is None, (
            f"{contract.agent_id} may invoke {tool_id!r}, which declares an OAuth "
            f"profile ({tool.oauth}) — this breaks the stateless-agent invariant "
            f"for the oauth_credentials table"
        )


@pytest.mark.parametrize("contract", _stateless_contracts(), ids=lambda c: c.agent_id)
def test_stateless_agents_hold_no_elevated_action_tool(contract) -> None:
    """No stateless agent may hold a tool performing a gated elevated action.

    Covers `org_permission_grants` the same way the test above covers
    `oauth_credentials`.
    """
    registry = _full_registry()
    granted = {g.tool_id for g in contract.allowed_tools}
    granted |= set(contract.invocable_tools or [])

    for tool_id in granted:
        if not registry.has(tool_id):
            continue
        tool = registry.resolve(tool_id)
        assert tool.permission is None, (
            f"{contract.agent_id} may invoke {tool_id!r}, which declares a "
            f"permission profile ({tool.permission}) — this breaks the "
            f"stateless-agent invariant for org_permission_grants"
        )


@pytest.mark.parametrize("contract", _stateless_contracts(), ids=lambda c: c.agent_id)
def test_stateless_agents_hold_no_drive_tool(contract) -> None:
    """Belt-and-braces: named Drive tools, independent of profile declarations.

    The two tests above key off the profile fields. This one keys off tool
    identity, so a Drive tool that somehow lost its profile would still be caught.
    """
    granted = {g.tool_id for g in contract.allowed_tools}
    granted |= set(contract.invocable_tools or [])
    overlap = granted & (EXPECTED_OAUTH_TOOL_IDS | EXPECTED_PERMISSION_TOOL_IDS)
    assert overlap == set(), (
        f"{contract.agent_id} holds Drive tool(s) {sorted(overlap)} — stateless "
        f"agents must have zero access to the Drive connector"
    )


def test_oauth_capable_tools_are_exactly_the_expected_set() -> None:
    """Pins which tools reach a customer's third-party account.

    A new connector landing makes this fail deliberately: that failure is the
    moment to re-check the stateless invariant above, not a nuisance to silence.
    """
    registry = _full_registry()
    with_oauth = {t.tool_id for t in registry.all() if t.oauth is not None}
    assert with_oauth == EXPECTED_OAUTH_TOOL_IDS, (
        f"OAuth-capable tool set changed: {sorted(with_oauth)}. Re-verify the "
        "stateless-agent invariant before updating this assertion."
    )


def test_permission_gated_tools_are_exactly_the_expected_set() -> None:
    """Pins which tools perform a gated elevated action.

    Sharing is the only one (integration_inputs.md 2.5, Q2.5b); file creation is
    deliberately NOT permission-gated because nothing leaves the customer's Drive.
    """
    registry = _full_registry()
    with_permission = {t.tool_id for t in registry.all() if t.permission is not None}
    assert with_permission == EXPECTED_PERMISSION_TOOL_IDS, (
        f"permission-gated tool set changed: {sorted(with_permission)}. Re-verify "
        "the stateless-agent invariant before updating this assertion."
    )


def test_drive_create_file_is_not_permission_gated() -> None:
    """Q2.5b's severity split, asserted rather than assumed.

    File creation stays inside the customer's own Drive; sharing sends data to a
    party the agent picks. Only the latter carries the third gate.
    """
    registry = _full_registry()
    create = registry.resolve("integration.drive_create_file")
    assert create.oauth is not None, "file creation still needs a live grant"
    assert create.permission is None, (
        "file creation must not be permission-gated: nothing leaves the "
        "customer's custody, so there is no recipient to pre-authorize"
    )
