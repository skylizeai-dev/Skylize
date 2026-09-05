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
    # GCP wiring included DELIBERATELY. Without it the registry holds no GCP
    # tool and every assertion below about GCP would pass by absence rather than
    # by the invariant actually holding.
    from skylize.dal.gcp_wif import InMemoryGcpWifRepository

    return default_tool_registry(
        credential_vault=vault,
        oauth_credentials=oauth,
        wif_repo=InMemoryGcpWifRepository(),
        gcp_executor_factory=lambda: None,
    )


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

def test_exactly_one_gcp_verb_is_registered_and_it_is_the_stop() -> None:
    """The externally-mutating GCP surface is exactly one verb, asserted.

    Replaces the foundation-era tripwire that asserted NO GCP tool existed. That
    assertion has now fired as designed and been discharged consciously: the stop
    verb was added. What must stay true from here is narrower and more useful -
    the registry holds that ONE verb and nothing else has been added beside it.

    A disk verb or an `addresses.delete` appearing here would fail this test, and
    both are excluded for the same recorded reason: they are irreversible, and
    the HITL replay path retries on transient failure
    (app/hitl/service.py:234-246), which is only safe for operations that
    converge on a state.
    """
    registry = _full_registry()
    gcp_tools = sorted(
        t.tool_id for t in registry.all()
        if "gcp" in t.tool_id.lower() or "compute" in t.tool_id.lower()
    )
    assert gcp_tools == ["integration.gcp_stop_instance"], (
        f"the GCP verb surface changed: {gcp_tools}. Adding an irreversible verb "
        "(disk delete, addresses.delete) is not safe under the HITL retry path - "
        "see app/gcp/actions.py."
    )


def test_the_gcp_verb_is_gated_by_the_federation_profile() -> None:
    """The stop verb must carry its `wif` profile, or it dispatches ungated.

    The profile is what makes the ToolProxy check that a federation exists, that
    the health probe has not already found it broken, and that the instance is on
    the customer's own allow-list. A verb that performs this action without it
    would reach a customer's infrastructure with none of those checks run.
    """
    tool = _full_registry().resolve("integration.gcp_stop_instance")
    assert tool.wif is not None, "the GCP stop verb lost its federation gate"
    assert tool.wif.project_field == "project"
    assert tool.wif.zone_field == "zone"
    assert tool.wif.instance_field == "instance"


def test_the_gcp_verb_is_not_spend_gated() -> None:
    """Stopping a customer's VM costs Skylize nothing, so it reserves nothing.

    A `ToolSpendProfile` here would place a hold on the customer's own LLM
    ceiling to perform a safety action - and, worse, the ceiling being breached
    is what TRIGGERS this action, so a spend gate could refuse the very
    containment the breach called for.
    """
    tool = _full_registry().resolve("integration.gcp_stop_instance")
    assert tool.spend is None


def test_only_the_infrastructure_executor_may_invoke_the_gcp_verb() -> None:
    """Registry-wide: exactly one contract holds the stop verb.

    The propose/act split depends on this. Any other contract gaining it - most
    of all a stateless observer - would let an agent that merely NOTICES an
    overspend also perform the containment.
    """
    from skylize.contracts.registry import MVP_REGISTRY

    holders = sorted(
        c.agent_id for c in MVP_REGISTRY.all()
        if "integration.gcp_stop_instance" in (c.invocable_tools or [])
        or any(g.tool_id == "integration.gcp_stop_instance" for g in c.allowed_tools)
    )
    assert holders == ["infrastructure_executor"], (
        f"the GCP stop verb is held by {holders}; only the stateful executor may "
        "hold it"
    )


def test_the_executor_can_never_auto_approve() -> None:
    """The executor's contract must keep a human-in-loop trigger.

    `_decide_agent_execution` defers on trigger PRESENCE
    (app/decision_engine/evaluator.py:230-245), and FIRST_EXTERNAL_LAUNCH is
    checked BEFORE the `defers_on_trigger_presence` opt-out - so it is the one
    trigger that cannot be suppressed. Losing it would let a customer's VM be
    stopped with no human verdict, and would also remove the `hitl_id` the action
    derives its retry-safe idempotency key from.
    """
    from skylize.contracts.base import HumanInLoopTrigger
    from skylize.contracts.registry import MVP_REGISTRY

    contract = MVP_REGISTRY.resolve("infrastructure_executor")
    assert HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH in contract.human_in_loop_triggers
    assert contract.memory_read_access == []
    assert contract.memory_write_access == []


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


# ---------------------------------------------------------------------------
# GitHub App foundation (migration 0026, app/github/*)
#
# integration_inputs.md 2.4 APPROVED 2026-09-05. This pass built the FOUNDATION
# only: platform key custody, token minting, and the connection-state probe. No
# tool is registered, so these are foundation-era tripwires in the same shape the
# GCP ones had before the stop verb landed - and like those, each says explicitly
# what discharging it consciously would look like.
# ---------------------------------------------------------------------------

def test_no_github_tool_is_registered_yet() -> None:
    """Foundation-era tripwire. Nothing GitHub-related is agent-reachable.

    This pass deliberately registers no verb: 2.4 Q2.4b.3's PR-merge verb and
    Q2.4e's webhook are both out of scope. When the merge verb lands, this test
    should be REPLACED by the narrower assertion that the GitHub verb surface is
    exactly that one verb - the way
    `test_exactly_one_gcp_verb_is_registered_and_it_is_the_stop` replaced GCP's
    equivalent. It must not simply be deleted.
    """
    registry = _full_registry()
    github_tools = sorted(
        t.tool_id for t in registry.all()
        if "github" in t.tool_id.lower() or "_pr_" in t.tool_id.lower()
    )
    assert github_tools == [], (
        f"a GitHub tool appeared: {github_tools}. The foundation pass registers "
        "none. If this is the approved PR-merge verb (2.4 Q2.4b.3), replace this "
        "test with an exact-surface assertion AND assert its HITL gate - do not "
        "just delete it."
    )


def test_no_branch_deletion_verb_exists_anywhere_in_the_registry() -> None:
    """The owner decision that must never be discharged, only kept.

    2.4 Q2.4b.2 chose VERB-SURFACE MINIMALISM over a runtime gate for branch
    deletion: Skylize never builds a delete-branch verb at all. That is a stronger
    property than gating one, because there is no gate to refactor around - and it
    is the only available answer, since `contents: write` is indivisible and
    cannot withhold ref deletion at the token layer (verified live 2026-09-05:
    DELETE /repos/{o}/{r}/git/refs/{ref} is the same permission as creating a
    branch and pushing).

    Unlike the tripwire above, this one is NOT expected to be discharged. A future
    pass adding a delete-branch verb is reversing an owner decision and needs a new
    2.4 approval, not a green test.
    """
    registry = _full_registry()
    offenders = sorted(
        t.tool_id for t in registry.all()
        if any(
            frag in t.tool_id.lower()
            for frag in ("delete_branch", "branch_delete", "delete_ref", "force_push")
        )
    )
    assert offenders == [], (
        f"a branch-deletion or force-push verb was registered: {offenders}. "
        "integration_inputs.md 2.4 Q2.4b.2 (APPROVED 2026-09-05) decided Skylize "
        "never builds one. Reversing that needs a new owner approval."
    )


def test_stateless_agents_hold_no_github_tool() -> None:
    """The five stateless agents get no GitHub path, now or by later accident.

    Vacuously true while no GitHub tool exists, and that is the point: it is here
    so that the moment one IS registered, this assertion is already standing rather
    than being something a future author has to remember to add.
    """
    for contract in _stateless_contracts():
        granted = {g.tool_id for g in contract.allowed_tools}
        granted |= set(contract.invocable_tools or [])
        github = sorted(t for t in granted if "github" in t.lower())
        assert github == [], (
            f"{contract.agent_id} was granted GitHub tools {github}. The five "
            "stateless agents (cfo_agent + the four safety agents) must have no "
            "path to a customer's third-party account."
        )


def test_github_credential_shape_holds_no_per_tenant_secret() -> None:
    """2.4 Q2.4d, asserted in code rather than left to the migration alone.

    The App private key is PLATFORM-level - one key shared across every tenant's
    installation - so the per-tenant row is non-secret and the row dataclass must
    expose no credential field. If someone starts storing a per-tenant token here,
    this fails, and the third-credential-shape decision has been reversed.
    """
    from dataclasses import fields

    from skylize.dal.github_app import GithubInstallationRow

    names = {f.name for f in fields(GithubInstallationRow)}
    leaked = {
        n for n in names
        if "token" in n or "secret" in n or "encrypted" in n or n == "key_id"
    }
    assert leaked == set(), (
        f"GithubInstallationRow grew credential field(s) {leaked}. Installation "
        "tokens are minted per call and never persisted (2.4 Q2.4d); the App "
        "private key is platform-level configuration, not tenant state."
    )
