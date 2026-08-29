"""The credential-state gate on the tool-call path (`OAuthCredentialService` in
`ToolProxy`).

Two properties under test:

  1. A tool declaring an `oauth` profile is denied when its grant is dead,
     absent, or unverifiable — with a credential-specific error type, never a
     spend one.
  2. **ORDERING**: that denial lands BEFORE the spend reservation. This is the
     point of the hook's placement, so it is asserted against the ledger's
     observed state rather than merely against the exception type — an
     outcome-only test would pass even if the hook were placed after the hold.

Mirrors tests/unit/test_tool_proxy_spend.py's harness: a real
`GovernanceAuthority` on the memory backend so tokens go through the production
mint/validate pipeline, and fakes for the two injected resources.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import BaseModel

from skylize.app.audit.service import AuditService
from skylize.app.credentials.oauth import (
    GrantNotConnected,
    GrantRevoked,
    RefreshUnavailable,
)
from skylize.app.governance import GovernanceAuthority
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
from skylize.events.memory_bus import InMemoryEventBus
from skylize.tools.base import (
    ToolContext,
    ToolCredentialDenied,
    ToolCredentialReconnectRequired,
    ToolCredentialUnavailable,
    ToolDefinition,
    ToolSpendDenied,
)
from skylize.tools.proxy import ToolProxy
from skylize.tools.registry import ToolRegistry

# Reuse the spend suite's fake ledger so the ordering assertion is made against
# the same ceiling semantics the real spend path uses.
from .test_tool_proxy_spend import FakeSpendRepo

ORG = "org_oauth_test"
PRINCIPAL = "devon"
AGENT = "oauth_test_agent"
OAUTH_TOOL = "test.oauth"              # oauth-only
OAUTH_SPEND_TOOL = "test.oauth_spend"  # oauth AND spend — the ordering probe
PROVIDER = "acme_docs"                 # deliberately not a real provider

TEST_KEY = "c2t5bGl6ZS1pbnRlZ3JhdGlvbi10ZXN0LWtleSF4MzI="


class _In(BaseModel):
    amount_minor: int = 100


class _Out(BaseModel):
    ok: bool


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _StubOAuth:
    """Stands in for `OAuthCredentialService`, recording call order.

    Only `ensure_fresh` is exercised by the proxy; raising from it reproduces
    each denial class without needing a token endpoint.
    """

    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.calls: list[tuple[str, str, str]] = []

    async def ensure_fresh(self, *, org_id, provider, label="", correlation_id=None):
        self.calls.append((org_id, provider, label))
        if self._raises is not None:
            raise self._raises
        return object()


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def _contract() -> AgentContract:
    return AgentContract(
        agent_id=AGENT,
        agent_role="OAuth gate test",
        authority_level="worker",
        department="engineering",
        input_schema="skylize.runtime.agent_runner.AgentRunInput",
        output_schema="skylize.runtime.agent_runner.AgentRunResult",
        allowed_tools=[
            ToolGrant(tool_id=OAUTH_TOOL, purpose="test"),
            ToolGrant(tool_id=OAUTH_SPEND_TOOL, purpose="test"),
        ],
        max_token_budget=8_000,
        max_execution_time_seconds=60,
        escalation_path=["human_owner"],
        failure_mode=FailureMode.FALLBACK_DEGRADED,
        memory_read_access=[],
        memory_write_access=[],
    )


def _registry() -> ToolRegistry:
    dispatched: list[str] = []

    async def handler(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        dispatched.append("called")
        return _Out(ok=True)

    reg = ToolRegistry([
        ToolDefinition(
            tool_id=OAUTH_TOOL, name="OAuth", description="needs a grant",
            input_schema=_In, output_schema=_Out, category="integration",
            handler=handler,
            oauth={"provider": PROVIDER},
        ),
        ToolDefinition(
            tool_id=OAUTH_SPEND_TOOL, name="OAuthSpend",
            description="needs a grant AND spends",
            input_schema=_In, output_schema=_Out, category="integration",
            handler=handler,
            oauth={"provider": PROVIDER},
            spend={"currency": "USD", "amount_field": "amount_minor"},
        ),
    ])
    reg.dispatched = dispatched  # type: ignore[attr-defined]
    return reg


def _authority():
    bus = InMemoryEventBus()
    audit = AuditService(bus, InMemoryAuditRepository())
    repo = InMemoryPrincipalRepository()
    repo.add_principal(
        Principal(
            principal_id=PRINCIPAL, org_id=ORG, display_name="Devon",
            authority_level="manager",
        )
    )
    for scope in (OAUTH_TOOL, OAUTH_SPEND_TOOL):
        repo.add_grant(
            org_id=ORG, principal_id=PRINCIPAL,
            grant=Grant(
                scope=scope, source=GrantSource.POSITION,
                valid_from=datetime.now(timezone.utc) - timedelta(days=1),
            ),
        )
    authority = GovernanceAuthority.build(
        repo=InMemoryGovernanceRepository(), audit=audit, bus=bus,
        registry=MVP_REGISTRY, settings=Settings(backend="memory"),
        principal_authority=PrincipalAuthorityService(repo),
    )
    return authority, bus, audit


def _proxy(authority, audit, *, oauth=None, spend_repo=None, registry=None) -> ToolProxy:
    return ToolProxy(
        registry=registry if registry is not None else _registry(),
        audit=audit,
        public_key=authority.public_key,
        live_state_for=authority.live_state_checker,
        spend_ledger=SpendLedger(spend_repo) if spend_repo is not None else None,
        oauth_credentials=oauth,
    )


async def _token(authority, contract, corr):
    return await authority.mint(
        contract, org_id=ORG, correlation_id=corr, on_behalf_of_principal=PRINCIPAL
    )


def _denied_reasons(bus) -> list[str]:
    return [
        e.payload.result_reason or ""
        for e in bus.published_of_type("audit.action_recorded")
        if e.payload.action_type == "tool.invoked" and e.payload.result == "denied"
    ]


# --------------------------------------------------------------------------- #
# Denial classes
# --------------------------------------------------------------------------- #


async def test_revoked_grant_denies_with_reconnect_required() -> None:
    authority, bus, audit = _authority()
    oauth = _StubOAuth(raises=GrantRevoked("grant is revoked"))
    proxy = _proxy(authority, audit, oauth=oauth)
    contract = _contract()
    corr = uuid4()
    token = await _token(authority, contract, corr)

    with pytest.raises(ToolCredentialReconnectRequired) as exc:
        await proxy.invoke(
            tool_id=OAUTH_TOOL, input_data={}, governance_token=token,
            contract=contract, org_id=ORG, correlation_id=corr,
        )

    assert exc.value.reconnect_required is True
    assert exc.value.failed_stage == "credential"
    assert any("credential:" in r for r in _denied_reasons(bus))


async def test_missing_grant_denies_with_reconnect_required() -> None:
    authority, bus, audit = _authority()
    oauth = _StubOAuth(raises=GrantNotConnected("not connected"))
    proxy = _proxy(authority, audit, oauth=oauth)
    contract = _contract()
    corr = uuid4()
    token = await _token(authority, contract, corr)

    with pytest.raises(ToolCredentialReconnectRequired):
        await proxy.invoke(
            tool_id=OAUTH_TOOL, input_data={}, governance_token=token,
            contract=contract, org_id=ORG, correlation_id=corr,
        )


async def test_transient_refresh_failure_denies_without_reconnect_flag() -> None:
    """"We could not check" must not tell the customer they disconnected us."""
    authority, bus, audit = _authority()
    oauth = _StubOAuth(raises=RefreshUnavailable("token endpoint unreachable"))
    proxy = _proxy(authority, audit, oauth=oauth)
    contract = _contract()
    corr = uuid4()
    token = await _token(authority, contract, corr)

    with pytest.raises(ToolCredentialUnavailable) as exc:
        await proxy.invoke(
            tool_id=OAUTH_TOOL, input_data={}, governance_token=token,
            contract=contract, org_id=ORG, correlation_id=corr,
        )
    assert exc.value.reconnect_required is False


async def test_no_oauth_service_wired_fails_closed() -> None:
    """An unenforced credential check is worse than none — it reads as enforced."""
    authority, bus, audit = _authority()
    proxy = _proxy(authority, audit, oauth=None)
    contract = _contract()
    corr = uuid4()
    token = await _token(authority, contract, corr)

    with pytest.raises(ToolCredentialUnavailable):
        await proxy.invoke(
            tool_id=OAUTH_TOOL, input_data={}, governance_token=token,
            contract=contract, org_id=ORG, correlation_id=corr,
        )


async def test_credential_denial_is_not_a_spend_denial() -> None:
    """The two hierarchies must stay separate: a dead grant and an exhausted
    budget have unrelated remedies."""
    authority, bus, audit = _authority()
    oauth = _StubOAuth(raises=GrantRevoked("dead"))
    proxy = _proxy(authority, audit, oauth=oauth)
    contract = _contract()
    corr = uuid4()
    token = await _token(authority, contract, corr)

    with pytest.raises(ToolCredentialDenied) as exc:
        await proxy.invoke(
            tool_id=OAUTH_TOOL, input_data={}, governance_token=token,
            contract=contract, org_id=ORG, correlation_id=corr,
        )
    assert not isinstance(exc.value, ToolSpendDenied)


async def test_valid_grant_dispatches_normally() -> None:
    authority, bus, audit = _authority()
    oauth = _StubOAuth()
    registry = _registry()
    proxy = _proxy(authority, audit, oauth=oauth, registry=registry)
    contract = _contract()
    corr = uuid4()
    token = await _token(authority, contract, corr)

    result = await proxy.invoke(
        tool_id=OAUTH_TOOL, input_data={}, governance_token=token,
        contract=contract, org_id=ORG, correlation_id=corr,
    )
    assert result.output_json() == {"ok": True}
    assert oauth.calls == [(ORG, PROVIDER, "")]


async def test_tool_without_oauth_profile_never_consults_the_service() -> None:
    """Opt-in: a tool that declares no profile keeps its pre-OAuth behaviour."""
    authority, bus, audit = _authority()
    oauth = _StubOAuth(raises=GrantRevoked("would deny if consulted"))

    async def handler(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        return _Out(ok=True)

    registry = ToolRegistry([
        ToolDefinition(
            tool_id=OAUTH_TOOL, name="Plain", description="no oauth",
            input_schema=_In, output_schema=_Out, category="compute",
            handler=handler,
        ),
    ])
    proxy = _proxy(authority, audit, oauth=oauth, registry=registry)
    contract = _contract()
    corr = uuid4()
    token = await _token(authority, contract, corr)

    result = await proxy.invoke(
        tool_id=OAUTH_TOOL, input_data={}, governance_token=token,
        contract=contract, org_id=ORG, correlation_id=corr,
    )
    assert result.output_json() == {"ok": True}
    assert oauth.calls == [], "a tool without an oauth profile must not be gated"


# --------------------------------------------------------------------------- #
# ORDERING — the reason the hook sits where it does
# --------------------------------------------------------------------------- #


async def test_dead_credential_denies_before_any_spend_hold_is_placed() -> None:
    """THE ordering guarantee.

    A tool that is BOTH oauth-gated and spend-capable, with a dead grant and a
    ledger that would happily accept the reservation. If the credential check ran
    after the spend reservation, a hold would have been placed (and then need
    unwinding). Asserting on the ledger's state — not just the exception type —
    is what makes this an ordering test rather than an outcome test.
    """
    authority, bus, audit = _authority()
    spend_repo = FakeSpendRepo(ceiling_minor=1_000_000)  # would definitely allow it
    oauth = _StubOAuth(raises=GrantRevoked("grant is revoked"))
    proxy = _proxy(authority, audit, oauth=oauth, spend_repo=spend_repo)
    contract = _contract()
    corr = uuid4()
    token = await _token(authority, contract, corr)

    with pytest.raises(ToolCredentialReconnectRequired):
        await proxy.invoke(
            tool_id=OAUTH_SPEND_TOOL, input_data={"amount_minor": 2_500},
            governance_token=token, contract=contract, org_id=ORG,
            correlation_id=corr,
        )

    assert oauth.calls, "the credential gate must have run"
    assert spend_repo.reserved_minor == 0, "no budget may be held for a denied call"
    assert spend_repo.held() == [], "no reservation row may exist"
    assert spend_repo.releases == [], (
        "nothing should need releasing — the hold must never have been placed, "
        "not placed-then-unwound"
    )
    assert spend_repo.commits == []


async def test_healthy_credential_still_reaches_the_spend_gate() -> None:
    """The converse: the credential gate must not swallow the spend gate. With a
    live grant and a ceiling of zero, the call is denied on BUDGET."""
    authority, bus, audit = _authority()
    spend_repo = FakeSpendRepo(ceiling_minor=0)
    oauth = _StubOAuth()
    proxy = _proxy(authority, audit, oauth=oauth, spend_repo=spend_repo)
    contract = _contract()
    corr = uuid4()
    token = await _token(authority, contract, corr)

    with pytest.raises(ToolSpendDenied) as exc:
        await proxy.invoke(
            tool_id=OAUTH_SPEND_TOOL, input_data={"amount_minor": 2_500},
            governance_token=token, contract=contract, org_id=ORG,
            correlation_id=corr,
        )
    assert exc.value.failed_stage == "budget"
    assert oauth.calls, "credential gate ran first, then the spend gate denied"
