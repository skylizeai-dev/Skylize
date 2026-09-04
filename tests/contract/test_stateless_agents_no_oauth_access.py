"""The stateless-agent invariant, asserted against the OAuth + connector surfaces.

Five agents are stateless by contract — `memory_read_access` and
`memory_write_access` are both empty — and no pass may give any of them a path to
`oauth_credentials`, `org_permission_grants`, or any connector tool (Drive, Asana).

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
    "integration.asana_create_task",
    "integration.asana_create_project",
    "integration.asana_add_project_member",
    "integration.notion_create_page",
    "integration.notion_create_database",
    "integration.notion_append_blocks",
}

#: Tools performing an elevated action gated by the org allow-list.
#: Drive sharing (2.5 Q2.5b) plus Asana's project membership verb (2.6 Q2.6b) —
#: every action that hands a customer's data or workspace to a party the agent
#: picks at run time, and nothing else. Asana's workspace-level `addUser` was
#: deliberately not built (2.6 Q2.6a: no granular scope covers it; would require
#: Full permissions, disproportionate to this platform's minimal-scope philosophy).
#: Notion contributes NOTHING here on purpose: its API exposes no sharing or
#: permission-granting endpoint at all (2.7 Q2.7b), so there is no elevated
#: action to gate — an absence that is a finding, not an omission.
EXPECTED_PERMISSION_TOOL_IDS = {
    "integration.drive_share_file",
    "integration.asana_add_project_member",
}

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
def test_stateless_agents_hold_no_connector_tool(contract) -> None:
    """Belt-and-braces: named connector tools, independent of profile declarations.

    The two tests above key off the profile fields. This one keys off tool
    identity, so a connector tool that somehow lost its profile would still be
    caught. Covers Drive and Asana.
    """
    granted = {g.tool_id for g in contract.allowed_tools}
    granted |= set(contract.invocable_tools or [])
    overlap = granted & (EXPECTED_OAUTH_TOOL_IDS | EXPECTED_PERMISSION_TOOL_IDS)
    assert overlap == set(), (
        f"{contract.agent_id} holds connector tool(s) {sorted(overlap)} — stateless "
        f"agents must have zero access to the Drive or Asana connectors"
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

    Drive sharing (2.5 Q2.5b) and Asana's project membership verb (2.6 Q2.6b). File,
    task, and project CREATION are deliberately NOT permission-gated: nothing
    leaves the customer's own account, so there is no recipient to pre-authorize.
    """
    registry = _full_registry()
    with_permission = {t.tool_id for t in registry.all() if t.permission is not None}
    assert with_permission == EXPECTED_PERMISSION_TOOL_IDS, (
        f"permission-gated tool set changed: {sorted(with_permission)}. Re-verify "
        "the stateless-agent invariant before updating this assertion."
    )


@pytest.mark.parametrize(
    "tool_id",
    [
        "integration.drive_create_file",
        "integration.asana_create_task",
        "integration.asana_create_project",
        "integration.notion_create_page",
        "integration.notion_create_database",
        "integration.notion_append_blocks",
    ],
)
def test_routine_creation_verbs_are_not_permission_gated(tool_id: str) -> None:
    """The severity split of 2.5 Q2.5b and 2.6 Q2.6b, asserted rather than assumed.

    Creation stays inside the customer's own account; sharing and membership send
    access to a party the agent picks. Only the latter carry the third gate.
    """
    registry = _full_registry()
    tool = registry.resolve(tool_id)
    assert tool.oauth is not None, f"{tool_id} still needs a live grant"
    assert tool.permission is None, (
        f"{tool_id} must not be permission-gated: nothing leaves the customer's "
        "custody, so there is no recipient to pre-authorize"
    )


def test_notion_declares_no_permission_gated_tool_at_all() -> None:
    """2.7 Q2.7b, asserted rather than left to the absence of a line of code.

    Notion's API has no sharing, permission-changing, or invitation endpoint
    (live-verified against developers.notion.com/reference/capabilities), so no
    Notion tool may ever carry the third gate. If one appears here, either Notion
    grew such an endpoint — in which case 2.7 needs revisiting — or somebody added
    a gate to make Notion look symmetrical with Drive and Asana, which would be
    gating an action that does not exist.
    """
    registry = _full_registry()
    notion = [t for t in registry.all() if t.tool_id.startswith("integration.notion_")]
    assert notion, "the Notion connector is not registered"
    assert all(t.oauth is not None for t in notion), (
        "every Notion tool still needs a live grant"
    )
    assert [t.tool_id for t in notion if t.permission is not None] == []


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


# ---------------------------------------------------------------------------
# GCP Workload Identity Federation (migration 0024, app/gcp/*)
# ---------------------------------------------------------------------------

def test_no_registered_tool_reaches_gcp_wif_this_pass() -> None:
    """The WIF foundation ships with NO tool attached to it, and that is asserted
    rather than assumed.

    The foundation pass deliberately builds the issuer surface, the trust-state
    table, the signing key, and the health probe, but NO Compute verb: nothing in
    the registry can stop a machine, because no such tool exists yet. This test
    is the tripwire that keeps that true until a later pass adds the verb
    consciously — at which point this test must be updated in the same commit
    that registers it, exactly as `EXPECTED_OAUTH_TOOL_IDS` forces for a new
    OAuth connector.

    It is deliberately registry-wide, not restricted to the stateless agents:
    while the verb does not exist, NO agent may hold one, and the strongest
    version of that statement is the one worth pinning.
    """
    registry = _full_registry()
    gcp_tools = [
        t.tool_id
        for t in registry.all()
        if "gcp" in t.tool_id.lower() or "compute" in t.tool_id.lower()
    ]
    assert gcp_tools == [], (
        f"a GCP/Compute tool is registered ({gcp_tools}) but this pass ships no "
        "trigger path for one. If a Compute verb is being added, update this test "
        "and the stateless allow-lists above in the same commit."
    )


@pytest.mark.parametrize("contract", _stateless_contracts(), ids=lambda c: c.agent_id)
def test_stateless_agents_cannot_reach_the_wif_signing_key_or_table(contract) -> None:
    """The stateless invariant, extended to the two things this pass introduces.

    A stateless agent acts only through tools resolved from the registry, so the
    reachability question reduces to: does any tool it may invoke touch the WIF
    signing key or the `gcp_wif_connections` table? While no GCP tool is
    registered at all (asserted directly above), the answer is structurally no —
    but pinning it per-contract means the day a Compute verb IS registered, every
    stateless contract is re-checked against it automatically rather than relying
    on someone remembering this constraint.
    """
    registry = _full_registry()
    granted = {g.tool_id for g in contract.allowed_tools}
    granted |= set(contract.invocable_tools or [])

    for tool_id in granted:
        if not registry.has(tool_id):
            continue
        assert "gcp" not in tool_id.lower(), (
            f"{contract.agent_id} may invoke {tool_id!r}, which reaches GCP "
            "Workload Identity Federation — this breaks the stateless-agent "
            "invariant for the gcp_wif_connections table and the WIF signing key"
        )
