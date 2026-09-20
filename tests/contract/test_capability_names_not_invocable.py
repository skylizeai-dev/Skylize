"""`invocable_tools` must hold only ids the model can actually be offered.

`ToolGrant.tool_id` spans two vocabularies: registry-backed tool ids, and the
capability names in `contracts.base.CAPABILITY_NAMES` that carry governance
scope but have no ToolDefinition. Both are legitimate in `allowed_tools`.

Only the first is legitimate in `invocable_tools`. `AgentExecutionService`
resolves each entry against the tool registry and DROPS an unregistered id with
a warning rather than failing, so a capability name there is silently not
offered to the model -- the contract says one thing and the run does another.

That gap is load-bearing wherever a contract's safety reasoning is written
against its stated manifest: the rationale describes a tool set the model never
receives. This test fails on that, so the contract and the run cannot disagree.
"""

from __future__ import annotations

import pytest

from skylize.app.audit.service import AuditService
from skylize.app.credentials.encryption import FernetEncryptor
from skylize.app.credentials.oauth import OAuthCredentialService
from skylize.app.credentials.vault import CredentialVault
from skylize.contracts.base import CAPABILITY_NAMES
from skylize.contracts.mvp import ALL_MVP_CONTRACTS
from skylize.dal.credentials import InMemoryCredentialRepository
from skylize.dal.memory import InMemoryAuditRepository
from skylize.dal.oauth_credentials import InMemoryOAuthCredentialRepository
from skylize.events.memory_bus import InMemoryEventBus
from skylize.tools.builtin import default_tool_registry
from skylize.tools.registry import ToolRegistry

TEST_KEY = FernetEncryptor.generate_key()


def _full_registry() -> ToolRegistry:
    """The registry as bootstrap wires it.

    Fully wired DELIBERATELY: the credential- and GCP-backed builders return []
    when their dependencies are absent, so a bare `default_tool_registry()`
    would make every id below look unregistered and this test would fail by
    absence instead of by the invariant.
    """
    audit = AuditService(InMemoryEventBus(), InMemoryAuditRepository())
    encryptor = FernetEncryptor(TEST_KEY)
    vault = CredentialVault(encryptor, InMemoryCredentialRepository(), audit)
    oauth = OAuthCredentialService(
        encryptor=encryptor,
        repo=InMemoryOAuthCredentialRepository(),
        audit=audit,
    )
    return default_tool_registry(
        credential_vault=vault,
        oauth_credentials=oauth,
        wif_repo=object(),
        gcp_executor_factory=object(),
        stripe_repo=object(),
        stripe_executor_factory=object(),
    )


_WITH_INVOCABLE = [c for c in ALL_MVP_CONTRACTS if c.invocable_tools]


@pytest.mark.parametrize("contract", _WITH_INVOCABLE, ids=lambda c: c.agent_id)
def test_invocable_tools_are_registry_backed(contract) -> None:
    """No capability name may sit in `invocable_tools`.

    A capability name is dropped at runtime (app/agents/execution.py), so the
    model is offered strictly fewer tools than the contract declares.
    """
    offending = sorted(set(contract.invocable_tools) & CAPABILITY_NAMES)
    assert not offending, (
        f"{contract.agent_id} lists capability name(s) {offending} in "
        f"invocable_tools. These have no ToolDefinition and are dropped at "
        f"runtime, so the model is offered only "
        f"{sorted(set(contract.invocable_tools) - CAPABILITY_NAMES)}. Either "
        f"remove them from invocable_tools, or re-derive any safety rationale "
        f"in the contract from what is actually offered."
    )


@pytest.mark.parametrize("contract", _WITH_INVOCABLE, ids=lambda c: c.agent_id)
def test_invocable_tools_resolve_in_the_registry(contract) -> None:
    """Every `invocable_tools` id must exist in the fully-wired registry.

    Catches a plain typo or a removed tool, which `_invocable_tools_subset_of_
    allowed` cannot see -- it compares against `allowed_tools`, which is just
    as free to hold an unregistered string.
    """
    registry = _full_registry()
    missing = sorted(t for t in contract.invocable_tools if not registry.has(t))
    assert not missing, (
        f"{contract.agent_id} lists {missing} in invocable_tools, which the "
        f"fully-wired tool registry does not contain. An unregistered id is "
        f"dropped at runtime, never offered to the model."
    )
