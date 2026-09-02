"""Asana's project-membership verb through the REAL `ToolProxy` permission stage.

`test_asana_tools.py` calls the handler directly, which proves the handler-level
backstop. This file proves the other half: the actual registered Asana tools, driven
through `ToolProxy.invoke` with a real `PermissionGate` over real
`org_permission_grants` rows, deny by default and never reach Asana when refused.

The distinction matters. A handler-level test can pass while the gate is misconfigured
(wrong action class, wrong field names, profile silently dropped by the registry), and
a gate-level test with stub tools can pass while the real tools are wired wrong. Only
this combination covers the path a production call actually takes.

Every assertion that matters is made on OBSERVED HTTP CALLS, not on which exception
surfaced: a test that only checked the exception type would pass even if the
membership change had already been sent to Asana.

Workspace-level `addUser` was removed (2.6 Q2.6a, owner decision): no published
Asana granular scope covers it, and enabling it would require Full permissions —
every endpoint, for every connected customer. Only `addMembers` (project-level)
ships, so this file covers `MEMBER_TOOL` and `TASK_TOOL` only.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from skylize.app.audit.service import AuditService
from skylize.app.credentials.asana_provider import ASANA_SCOPES
from skylize.app.credentials.encryption import FernetEncryptor
from skylize.app.credentials.oauth import OAuthCredentialService
from skylize.app.governance import GovernanceAuthority
from skylize.app.permissions.gate import PermissionGate
from skylize.app.principal.models import Grant, GrantSource, Principal
from skylize.app.principal.provider import (
    InMemoryPrincipalRepository,
    PrincipalAuthorityService,
)
from skylize.config import Settings
from skylize.contracts.base import AgentContract, FailureMode, ToolGrant
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.memory import InMemoryAuditRepository, InMemoryGovernanceRepository
from skylize.dal.oauth_credentials import (
    InMemoryOAuthCredentialRepository,
    OAuthCredentialRow,
)
from skylize.dal.permission_grants import (
    InMemoryPermissionGrantRepository,
    PermissionGrantRow,
)
from skylize.events.memory_bus import InMemoryEventBus
from skylize.tools.base import ToolPermissionTierDenied
from skylize.tools.builtin.asana_tools import (
    ASANA_ADD_PROJECT_MEMBER_ACTION_CLASS,
    ASANA_PROVIDER,
    build_asana_add_project_member_tool,
    build_asana_create_task_tool,
)
from skylize.tools.proxy import ToolProxy
from skylize.tools.registry import ToolRegistry

ORG = "org_asana_proxy"
PRINCIPAL = "devon"
AGENT = "asana_proxy_agent"
TEST_KEY = "c2t5bGl6ZS1pbnRlZ3JhdGlvbi10ZXN0LWtleSF4MzI="

MEMBER_TOOL = "integration.asana_add_project_member"
TASK_TOOL = "integration.asana_create_task"


def _patch_http(monkeypatch) -> list[httpx.Request]:
    """Record every outbound request; answer all of them successfully.

    Answering 201 to everything is deliberate: if the gate leaks, the call
    SUCCEEDS and the recorded request proves it, rather than the test being saved
    by an unrelated transport error.
    """
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"data": {"gid": "1", "name": "x"}})

    real_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(recording)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
    return seen


def _row(action_class: str, pattern: str, *, role: str = "writer", link: bool = False):
    now = datetime.now(timezone.utc)
    return PermissionGrantRow(
        grant_id=uuid.uuid4(), org_id=ORG, action_class=action_class,
        grantee_pattern=pattern, max_role=role,  # type: ignore[arg-type]
        allow_link_sharing=link, created_at=now, updated_at=now,
    )


async def _gate(*rows) -> PermissionGate:
    repo = InMemoryPermissionGrantRepository()
    for r in rows:
        await repo.insert(r)
    return PermissionGate(repo)


async def _oauth() -> OAuthCredentialService:
    enc = FernetEncryptor(TEST_KEY)
    repo = InMemoryOAuthCredentialRepository()
    now = datetime.now(timezone.utc)
    await repo.insert(OAuthCredentialRow(
        cred_id=uuid.uuid4(), org_id=ORG, provider=ASANA_PROVIDER, label="",
        provider_account_id="acct", key_id="platform-fernet-v1",
        encrypted_access_token=enc.encrypt("asana-access-token"),
        encrypted_refresh_token=enc.encrypt("asana-refresh"),
        expires_at=now + timedelta(hours=1), scopes=ASANA_SCOPES,
        connection_state="valid", state_reason=None,
        created_at=now, updated_at=now, refreshed_at=None,
    ))
    return OAuthCredentialService(
        encryptor=enc, repo=repo,
        audit=AuditService(InMemoryEventBus(), InMemoryAuditRepository()),
    )


def _contract() -> AgentContract:
    return AgentContract(
        agent_id=AGENT, agent_role="Asana gate test",
        authority_level="worker", department="engineering",
        input_schema="skylize.runtime.agent_runner.AgentRunInput",
        output_schema="skylize.runtime.agent_runner.AgentRunResult",
        allowed_tools=[
            ToolGrant(tool_id=MEMBER_TOOL, purpose="test"),
            ToolGrant(tool_id=TASK_TOOL, purpose="test"),
        ],
        max_token_budget=8_000, max_execution_time_seconds=60,
        escalation_path=["human_owner"], failure_mode=FailureMode.FALLBACK_DEGRADED,
        memory_read_access=[], memory_write_access=[],
    )


def _authority():
    bus = InMemoryEventBus()
    audit = AuditService(bus, InMemoryAuditRepository())
    repo = InMemoryPrincipalRepository()
    repo.add_principal(
        Principal(principal_id=PRINCIPAL, org_id=ORG, display_name="Devon",
                  authority_level="manager")
    )
    for scope in (MEMBER_TOOL, TASK_TOOL):
        repo.add_grant(
            org_id=ORG, principal_id=PRINCIPAL,
            grant=Grant(scope=scope, source=GrantSource.POSITION,
                        valid_from=datetime.now(timezone.utc) - timedelta(days=1)),
        )
    authority = GovernanceAuthority.build(
        repo=InMemoryGovernanceRepository(), audit=audit, bus=bus,
        registry=MVP_REGISTRY, settings=Settings(backend="memory"),
        principal_authority=PrincipalAuthorityService(repo),
    )
    return authority, audit


async def _invoke(gate, tool_id: str, **input_data):
    """Drive one real Asana tool through the real proxy. Returns the result."""
    authority, audit = _authority()
    oauth = await _oauth()
    registry = ToolRegistry([
        build_asana_add_project_member_tool(oauth),
        build_asana_create_task_tool(oauth),
    ])
    proxy = ToolProxy(
        registry=registry, audit=audit,
        public_key=authority.public_key,
        live_state_for=authority.live_state_checker,
        oauth_credentials=oauth,
        permission_gate=gate,
    )
    contract = _contract()
    corr = uuid4()
    token = await authority.mint(
        contract, org_id=ORG, correlation_id=corr, on_behalf_of_principal=PRINCIPAL
    )
    return await proxy.invoke(
        tool_id=tool_id, input_data=input_data,
        governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
    )


# ---------------------------------------------------------------------------
# HARD GATE: deny by default
# ---------------------------------------------------------------------------

async def test_project_member_denied_when_org_authorized_nobody(monkeypatch) -> None:
    """An empty allow-list denies. Absence is never an implicit allow."""
    calls = _patch_http(monkeypatch)
    gate = await _gate()  # no rows at all
    with pytest.raises(ToolPermissionTierDenied, match="pre-authorized no recipient"):
        await _invoke(gate, MEMBER_TOOL, project_gid="p1", grantee="alice@example.com")
    assert calls == [], "denied membership change must never reach Asana"


async def test_unmatched_recipient_is_denied(monkeypatch) -> None:
    calls = _patch_http(monkeypatch)
    gate = await _gate(_row(ASANA_ADD_PROJECT_MEMBER_ACTION_CLASS, "example.com"))
    with pytest.raises(ToolPermissionTierDenied, match="matches no pre-authorized pattern"):
        await _invoke(gate, MEMBER_TOOL, project_gid="p1", grantee="mallory@evil.test")
    assert calls == []


async def test_grant_for_a_different_action_class_is_denied(monkeypatch) -> None:
    """A pre-authorization for a different elevated action must not carry over."""
    calls = _patch_http(monkeypatch)
    gate = await _gate(_row("drive.permissions.create", "alice@example.com"))
    with pytest.raises(ToolPermissionTierDenied, match="pre-authorized no recipient"):
        await _invoke(gate, MEMBER_TOOL, project_gid="p1", grantee="alice@example.com")
    assert calls == [], "an unrelated action class must not authorize Asana membership"


async def test_reader_max_role_authorizes_nothing_for_asana(monkeypatch) -> None:
    """Q2.6e's documented consequence, asserted rather than left as prose.

    Every Asana membership request arrives as `writer` (the schema pins it), and
    the gate ANDs grantee-match with role rank. So an org row capped at `reader`
    matches the recipient and still denies — the intended fail-closed behaviour.
    """
    calls = _patch_http(monkeypatch)
    gate = await _gate(
        _row(ASANA_ADD_PROJECT_MEMBER_ACTION_CLASS, "alice@example.com", role="reader")
    )
    with pytest.raises(ToolPermissionTierDenied, match="exceeds the maximum"):
        await _invoke(gate, MEMBER_TOOL, project_gid="p1", grantee="alice@example.com")
    assert calls == []


async def test_commenter_max_role_also_authorizes_nothing(monkeypatch) -> None:
    calls = _patch_http(monkeypatch)
    gate = await _gate(
        _row(ASANA_ADD_PROJECT_MEMBER_ACTION_CLASS, "example.com", role="commenter")
    )
    with pytest.raises(ToolPermissionTierDenied, match="exceeds the maximum"):
        await _invoke(gate, MEMBER_TOOL, project_gid="p1", grantee="alice@example.com")
    assert calls == []


# ---------------------------------------------------------------------------
# The authorized path
# ---------------------------------------------------------------------------

async def test_authorized_project_membership_reaches_asana(monkeypatch) -> None:
    """The positive control: without it, every denial test above proves nothing."""
    calls = _patch_http(monkeypatch)
    gate = await _gate(
        _row(ASANA_ADD_PROJECT_MEMBER_ACTION_CLASS, "example.com", role="writer")
    )
    result = await _invoke(
        gate, MEMBER_TOOL, project_gid="p1", grantee="alice@example.com"
    )
    assert len(calls) == 1
    assert str(calls[0].url).endswith("/projects/p1/addMembers")
    assert result.output.grantee == "alice@example.com"
    assert result.output.role == "writer"


async def test_routine_task_creation_needs_no_permission_row(monkeypatch) -> None:
    """Q2.6b: the routine verb runs with an EMPTY allow-list.

    If this ever starts requiring a grant, the severity split has been broken in
    the direction of over-gating, which is just as much a defect as under-gating.
    """
    calls = _patch_http(monkeypatch)
    gate = await _gate()  # no rows at all
    result = await _invoke(gate, TASK_TOOL, name="Draft", workspace_gid="w1")
    assert len(calls) == 1
    assert str(calls[0].url).endswith("/tasks")
    assert result.output.task_gid == "1"


# ---------------------------------------------------------------------------
# Fail-closed when the gate itself is absent
# ---------------------------------------------------------------------------

async def test_membership_fails_closed_with_no_gate_wired(monkeypatch) -> None:
    """A process with no `PermissionGate` must refuse, not dispatch ungated."""
    from skylize.tools.base import ToolPermissionUnavailable

    calls = _patch_http(monkeypatch)
    with pytest.raises(ToolPermissionUnavailable, match="no permission gate is wired"):
        await _invoke(None, MEMBER_TOOL, project_gid="p1", grantee="alice@example.com")
    assert calls == []
