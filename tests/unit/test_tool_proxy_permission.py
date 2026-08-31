"""The elevated-action gate on the tool-call path (`PermissionGate` in `ToolProxy`).

Three properties under test:

  1. A tool declaring a `permission` profile is denied when the org has not
     pre-authorized the recipient/role — with a permission-tier error type,
     distinct from both credential and spend denials.
  2. **DENY BY DEFAULT (hard security requirement).** A tool that performs an
     elevated action but declares NO profile must not be able to execute it. The
     gate is opt-in, so this is enforced at the handler via the required
     `ToolContext.permission_grant`. Tested by registering exactly that
     misconfiguration and asserting the side effect never runs.
  3. **ORDERING**: the permission check lands after the OAuth stage and before
     the spend reservation — asserted against observed ledger state and call
     order, not merely against which exception surfaced.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import BaseModel

from skylize.app.audit.service import AuditService
from skylize.app.governance import GovernanceAuthority
from skylize.app.permissions.gate import PermissionGate
from skylize.app.principal.models import Grant, GrantSource, Principal
from skylize.app.principal.provider import (
    InMemoryPrincipalRepository,
    PrincipalAuthorityService,
)
from skylize.app.principal.spend import SpendLedger
from skylize.config import Settings
from skylize.contracts.base import AgentContract, FailureMode, ToolGrant
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.memory import InMemoryAuditRepository, InMemoryGovernanceRepository
from skylize.dal.permission_grants import (
    InMemoryPermissionGrantRepository,
    PermissionGrantRow,
)
from skylize.events.memory_bus import InMemoryEventBus
from skylize.tools.base import (
    ToolContext,
    ToolCredentialDenied,
    ToolDefinition,
    ToolPermissionTierDenied,
    ToolPermissionUnavailable,
    ToolSpendDenied,
)
from skylize.tools.proxy import ToolProxy
from skylize.tools.registry import ToolRegistry

from .test_tool_proxy_spend import FakeSpendRepo

ORG = "org_perm_proxy"
PRINCIPAL = "devon"
AGENT = "perm_test_agent"
SHARE_TOOL = "test.share"
SHARE_SPEND_TOOL = "test.share_spend"
UNGATED_SHARE_TOOL = "test.share_ungated"
ACTION = "drive.permissions.create"


class _ShareIn(BaseModel):
    grantee: str = "alice@example.com"
    role: str = "reader"
    amount_minor: int = 100


class _ShareOut(BaseModel):
    ok: bool


class _StubOAuth:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.calls: list[str] = []

    async def ensure_fresh(self, *, org_id, provider, label="", correlation_id=None):
        self.calls.append(provider)
        if self._raises is not None:
            raise self._raises
        return object()


def _row(pattern: str, *, role: str = "writer", link: bool = False):
    now = datetime.now(timezone.utc)
    return PermissionGrantRow(
        grant_id=uuid.uuid4(), org_id=ORG, action_class=ACTION,
        grantee_pattern=pattern, max_role=role,  # type: ignore[arg-type]
        allow_link_sharing=link, created_at=now, updated_at=now,
    )


async def _gate(*rows) -> PermissionGate:
    repo = InMemoryPermissionGrantRepository()
    for r in rows:
        await repo.insert(r)
    return PermissionGate(repo)


def _contract() -> AgentContract:
    return AgentContract(
        agent_id=AGENT, agent_role="Permission gate test",
        authority_level="worker", department="engineering",
        input_schema="skylize.runtime.agent_runner.AgentRunInput",
        output_schema="skylize.runtime.agent_runner.AgentRunResult",
        allowed_tools=[
            ToolGrant(tool_id=SHARE_TOOL, purpose="test"),
            ToolGrant(tool_id=SHARE_SPEND_TOOL, purpose="test"),
            ToolGrant(tool_id=UNGATED_SHARE_TOOL, purpose="test"),
        ],
        max_token_budget=8_000, max_execution_time_seconds=60,
        escalation_path=["human_owner"], failure_mode=FailureMode.FALLBACK_DEGRADED,
        memory_read_access=[], memory_write_access=[],
    )


def _registry(executed: list[str]) -> ToolRegistry:
    """Registry containing a correctly-gated tool AND a deliberately
    MISCONFIGURED one that performs the same elevated action with no profile."""

    async def gated_handler(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        # Mirrors the real Drive share handler's deny-by-default backstop.
        if ctx.permission_grant is None:
            raise ToolPermissionUnavailable(
                "dispatched without a PermissionGrant; refusing elevated action"
            )
        executed.append(f"shared:{ctx.permission_grant.grantee}")
        return _ShareOut(ok=True)

    return ToolRegistry([
        ToolDefinition(
            tool_id=SHARE_TOOL, name="Share", description="shares",
            input_schema=_ShareIn, output_schema=_ShareOut, category="integration",
            handler=gated_handler,
            oauth={"provider": "acme_docs"},
            permission={
                "action_class": ACTION, "grantee_field": "grantee", "role_field": "role",
            },
        ),
        ToolDefinition(
            tool_id=SHARE_SPEND_TOOL, name="ShareSpend", description="shares and spends",
            input_schema=_ShareIn, output_schema=_ShareOut, category="integration",
            handler=gated_handler,
            oauth={"provider": "acme_docs"},
            permission={
                "action_class": ACTION, "grantee_field": "grantee", "role_field": "role",
            },
            spend={"currency": "USD", "amount_field": "amount_minor"},
        ),
        # THE MISCONFIGURATION the hard gate is about: same elevated handler,
        # no `permission` profile declared. The gate never runs for it.
        ToolDefinition(
            tool_id=UNGATED_SHARE_TOOL, name="ShareUngated",
            description="shares but forgot to declare a permission profile",
            input_schema=_ShareIn, output_schema=_ShareOut, category="integration",
            handler=gated_handler,
        ),
    ])


def _authority():
    bus = InMemoryEventBus()
    audit = AuditService(bus, InMemoryAuditRepository())
    repo = InMemoryPrincipalRepository()
    repo.add_principal(
        Principal(principal_id=PRINCIPAL, org_id=ORG, display_name="Devon",
                  authority_level="manager")
    )
    for scope in (SHARE_TOOL, SHARE_SPEND_TOOL, UNGATED_SHARE_TOOL):
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
    return authority, bus, audit


def _proxy(authority, audit, *, registry, gate=None, oauth=None, spend_repo=None):
    return ToolProxy(
        registry=registry, audit=audit,
        public_key=authority.public_key,
        live_state_for=authority.live_state_checker,
        spend_ledger=SpendLedger(spend_repo) if spend_repo is not None else None,
        oauth_credentials=oauth if oauth is not None else _StubOAuth(),
        permission_gate=gate,
    )


async def _token(authority, contract, corr):
    return await authority.mint(
        contract, org_id=ORG, correlation_id=corr, on_behalf_of_principal=PRINCIPAL
    )


async def _invoke(proxy, contract, authority, tool_id, **input_over):
    corr = uuid4()
    token = await _token(authority, contract, corr)
    return await proxy.invoke(
        tool_id=tool_id, input_data={**input_over},
        governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
    )


# ---------------------------------------------------------------------------
# HARD GATE: deny by default
# ---------------------------------------------------------------------------

async def test_elevated_tool_without_a_profile_cannot_execute() -> None:
    """THE hard security requirement.

    A tool whose handler performs a sharing action but which declares NO
    `ToolPermissionProfile` skips the gate entirely — that is what "opt-in"
    means. It must still be impossible for the side effect to run. The handler
    receives no `PermissionGrant` and fails closed.

    Asserted on the side-effect list, not the exception type: a test that only
    checked the raised class would pass even if the share had already happened.
    """
    authority, bus, audit = _authority()
    executed: list[str] = []
    gate = await _gate(_row("example.com", role="writer"))  # would ALLOW if consulted
    proxy = _proxy(authority, audit, registry=_registry(executed), gate=gate)
    contract = _contract()

    with pytest.raises(ToolPermissionUnavailable):
        await _invoke(proxy, contract, authority, UNGATED_SHARE_TOOL)

    assert executed == [], "an unclassified elevated action must never execute"


async def test_no_gate_wired_fails_closed() -> None:
    """An unenforced gate is worse than none — it reads as enforced."""
    authority, bus, audit = _authority()
    executed: list[str] = []
    proxy = _proxy(authority, audit, registry=_registry(executed), gate=None)
    contract = _contract()

    with pytest.raises(ToolPermissionUnavailable):
        await _invoke(proxy, contract, authority, SHARE_TOOL)
    assert executed == []


async def test_empty_allow_list_denies_and_does_not_execute() -> None:
    authority, bus, audit = _authority()
    executed: list[str] = []
    proxy = _proxy(authority, audit, registry=_registry(executed), gate=await _gate())
    contract = _contract()

    with pytest.raises(ToolPermissionTierDenied) as exc:
        await _invoke(proxy, contract, authority, SHARE_TOOL)
    assert exc.value.failed_stage == "permission_tier"
    assert executed == []


# ---------------------------------------------------------------------------
# Denial typing
# ---------------------------------------------------------------------------

async def test_permission_denial_is_neither_a_spend_nor_a_credential_denial() -> None:
    """Three unrelated remedies must stay three distinct types."""
    authority, bus, audit = _authority()
    proxy = _proxy(authority, audit, registry=_registry([]), gate=await _gate())
    contract = _contract()

    with pytest.raises(ToolPermissionTierDenied) as exc:
        await _invoke(proxy, contract, authority, SHARE_TOOL)
    assert not isinstance(exc.value, ToolSpendDenied)
    assert not isinstance(exc.value, ToolCredentialDenied)


async def test_unauthorized_recipient_is_deferrable_but_misconfig_is_not() -> None:
    """`defer_to_human` distinguishes "operator could approve this" from
    "something is broken"; a future HITL router branches on it."""
    authority, bus, audit = _authority()
    contract = _contract()

    proxy = _proxy(authority, audit, registry=_registry([]), gate=await _gate())
    with pytest.raises(ToolPermissionTierDenied) as denied:
        await _invoke(proxy, contract, authority, SHARE_TOOL)
    assert denied.value.defer_to_human is True

    proxy2 = _proxy(authority, audit, registry=_registry([]), gate=None)
    with pytest.raises(ToolPermissionUnavailable) as unavailable:
        await _invoke(proxy2, contract, authority, SHARE_TOOL)
    assert unavailable.value.defer_to_human is False


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

async def test_authorized_share_executes_with_the_granted_values() -> None:
    authority, bus, audit = _authority()
    executed: list[str] = []
    gate = await _gate(_row("example.com", role="writer"))
    proxy = _proxy(authority, audit, registry=_registry(executed), gate=gate)
    contract = _contract()

    result = await _invoke(proxy, contract, authority, SHARE_TOOL)
    assert result.output_json() == {"ok": True}
    assert executed == ["shared:alice@example.com"]


async def test_tool_without_permission_profile_is_unaffected_by_the_gate() -> None:
    """Opt-in: a non-elevated tool never consults the gate."""
    authority, bus, audit = _authority()

    async def plain(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        return _ShareOut(ok=True)

    registry = ToolRegistry([
        ToolDefinition(
            tool_id=SHARE_TOOL, name="Plain", description="no elevated action",
            input_schema=_ShareIn, output_schema=_ShareOut, category="compute",
            handler=plain,
        )
    ])
    proxy = _proxy(authority, audit, registry=registry, gate=await _gate())
    contract = _contract()
    result = await _invoke(proxy, contract, authority, SHARE_TOOL)
    assert result.output_json() == {"ok": True}


# ---------------------------------------------------------------------------
# ORDERING
# ---------------------------------------------------------------------------

async def test_permission_denial_lands_before_any_spend_hold() -> None:
    """A denied share must not first reserve budget it will never use."""
    authority, bus, audit = _authority()
    executed: list[str] = []
    spend_repo = FakeSpendRepo(ceiling_minor=1_000_000)  # would allow it
    proxy = _proxy(
        authority, audit, registry=_registry(executed),
        gate=await _gate(), spend_repo=spend_repo,
    )
    contract = _contract()

    with pytest.raises(ToolPermissionTierDenied):
        await _invoke(proxy, contract, authority, SHARE_SPEND_TOOL)

    assert spend_repo.reserved_minor == 0, "no budget may be held for a denied share"
    assert spend_repo.held() == []
    assert spend_repo.releases == [], "the hold must never have been placed at all"
    assert executed == []


async def test_credential_denial_lands_before_the_permission_check() -> None:
    """The OAuth stage runs first: there is nothing to authorize on a call whose
    credential is dead, and evaluating the recipient anyway would leak that the
    share would otherwise have been allowed."""
    from skylize.app.credentials.oauth import GrantRevoked

    authority, bus, audit = _authority()
    repo = InMemoryPermissionGrantRepository()
    calls: list[str] = []

    class _RecordingGate(PermissionGate):
        async def authorize(self, **kw):  # noqa: ANN003
            calls.append("permission")
            return await super().authorize(**kw)

    proxy = _proxy(
        authority, audit, registry=_registry([]),
        gate=_RecordingGate(repo),
        oauth=_StubOAuth(raises=GrantRevoked("grant revoked")),
    )
    contract = _contract()

    with pytest.raises(ToolCredentialDenied):
        await _invoke(proxy, contract, authority, SHARE_TOOL)
    assert calls == [], "permission gate must not run once the credential is dead"


async def test_healthy_share_still_reaches_the_spend_gate() -> None:
    """The permission gate must not swallow the spend gate."""
    authority, bus, audit = _authority()
    spend_repo = FakeSpendRepo(ceiling_minor=0)
    proxy = _proxy(
        authority, audit, registry=_registry([]),
        gate=await _gate(_row("example.com", role="writer")), spend_repo=spend_repo,
    )
    contract = _contract()

    with pytest.raises(ToolSpendDenied) as exc:
        await _invoke(proxy, contract, authority, SHARE_SPEND_TOOL)
    assert exc.value.failed_stage == "budget"
