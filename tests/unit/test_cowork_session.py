"""Mid-session grant revocation, and where it lands.

The property (Q3): a co-work session may run for hours, but a grant revoked at
09:05 must not keep working until the session ends. A short token plus a refresh
that RE-AUTHORIZES -- rather than merely extends -- is what makes that true.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from skylize.app.audit.service import AuditService
from skylize.app.cowork import COWORK_TOKEN_TTL_MINUTES, CoworkSessionService
from skylize.app.governance import GovernanceAuthority
from skylize.app.principal.errors import AuthorityExceeded
from skylize.app.principal.models import Grant, GrantSource, Principal
from skylize.app.principal.provider import (
    InMemoryPrincipalRepository,
    PrincipalAuthorityService,
)
from skylize.config import Settings
from skylize.contracts.mvp.cowork import cowork_agent
from skylize.contracts.registry import MVP_REGISTRY
from skylize.contracts.token import ValidationStage, validate_tool_call
from skylize.dal.memory import InMemoryAuditRepository, InMemoryGovernanceRepository
from skylize.events.memory_bus import InMemoryEventBus

ORG = "org_test"
DEVON = "devon"
LLM = "llm.generate"
MEM = "memory.search"


def _grant(scope: str, source=GrantSource.POSITION, justification=None) -> Grant:
    return Grant(
        scope=scope,
        source=source,
        valid_from=datetime.now(timezone.utc) - timedelta(days=1),
        justification=justification,
    )


def _session(*, grants):
    bus = InMemoryEventBus()
    audit = AuditService(bus, InMemoryAuditRepository())
    repo = InMemoryPrincipalRepository()
    repo.add_principal(
        Principal(
            principal_id=DEVON, org_id=ORG, display_name="Devon", authority_level="manager"
        )
    )
    for g in grants:
        repo.add_grant(org_id=ORG, principal_id=DEVON, grant=g)
    # One provider instance shared by the Authority's mint gate and the session,
    # so both read the same live repo -- a grant added mid-test is visible to
    # each of them, which is what makes the revocation tests meaningful.
    provider = PrincipalAuthorityService(repo)
    authority = GovernanceAuthority.build(
        repo=InMemoryGovernanceRepository(),
        audit=audit,
        bus=bus,
        registry=MVP_REGISTRY,
        settings=Settings(backend="memory"),
        principal_authority=provider,
    )
    service = CoworkSessionService(
        authority=authority, contract=cowork_agent, principal_authority=provider
    )
    return service, authority, repo


async def test_session_token_is_cowork_and_principal_bound() -> None:
    session, _, _ = _session(grants=[_grant(LLM), _grant(MEM)])
    token = await session.start(org_id=ORG, principal_id=DEVON)

    assert token.token_version == "1.1"
    assert token.on_behalf_of is not None
    assert token.on_behalf_of.principal_id == DEVON
    assert token.on_behalf_of.session_kind == "cowork"


async def test_session_token_ttl_is_short() -> None:
    """A long session must not imply a long token -- that window is the whole
    exposure a mid-session revocation has to survive."""
    session, _, _ = _session(grants=[_grant(LLM)])
    token = await session.start(org_id=ORG, principal_id=DEVON)
    lifetime = token.expires_at - token.issued_at
    assert lifetime == timedelta(minutes=COWORK_TOKEN_TTL_MINUTES)
    assert lifetime <= timedelta(minutes=15)


async def test_refresh_narrows_scope_after_a_grant_is_revoked() -> None:
    """THE Q3 property. The session keeps running; the authority behind it does
    not. The refreshed token simply does not carry the withdrawn scope."""
    session, _, repo = _session(grants=[_grant(LLM), _grant(MEM)])
    before = await session.start(org_id=ORG, principal_id=DEVON)
    assert set(before.scope) == {LLM, MEM}

    # Mid-session: the employee loses memory.search.
    repo.add_grant(
        org_id=ORG,
        principal_id=DEVON,
        grant=_grant(MEM, GrantSource.EXPLICIT_DENY, "offboarding, ticket SEC-200"),
    )

    after = await session.refresh(org_id=ORG, principal_id=DEVON)
    assert MEM not in after.scope
    assert LLM in after.scope


async def test_refresh_recomputes_the_authority_fingerprint() -> None:
    """Proof the refresh really re-ran compile_authority rather than reissuing
    the same claim: the fingerprint is a hash of the effective scope set, so it
    can only change if the authority was recompiled."""
    session, _, repo = _session(grants=[_grant(LLM), _grant(MEM)])
    before = await session.start(org_id=ORG, principal_id=DEVON)
    repo.add_grant(
        org_id=ORG,
        principal_id=DEVON,
        grant=_grant(MEM, GrantSource.EXPLICIT_DENY, "offboarding, ticket SEC-200"),
    )
    after = await session.refresh(org_id=ORG, principal_id=DEVON)

    assert (
        after.on_behalf_of.authority_fingerprint
        != before.on_behalf_of.authority_fingerprint
    )


async def test_refresh_is_refused_outright_when_all_authority_is_withdrawn() -> None:
    """If the contract's whole manifest is now outside the human's authority the
    refresh does not quietly return an empty token -- it raises, and the session
    cannot continue."""
    session, _, repo = _session(grants=[_grant(LLM), _grant(MEM)])
    await session.start(org_id=ORG, principal_id=DEVON)

    for scope in (LLM, MEM):
        repo.add_grant(
            org_id=ORG,
            principal_id=DEVON,
            grant=_grant(scope, GrantSource.EXPLICIT_DENY, "offboarded, ticket SEC-201"),
        )

    with pytest.raises(AuthorityExceeded):
        await session.refresh(org_id=ORG, principal_id=DEVON)


async def test_the_old_token_dies_before_the_refresh_too() -> None:
    """The second, independent path: invalidation kills tokens ALREADY IN FLIGHT
    at their next call, so the exposure window is "until the next call", not even
    "until the next refresh"."""
    session, authority, repo = _session(grants=[_grant(LLM)])
    token = await session.start(org_id=ORG, principal_id=DEVON)

    def _check():
        return validate_tool_call(
            token=token,
            public_key=authority.public_key,
            requested_tool_id=LLM,
            contract_allowed_tool_ids={t.tool_id for t in cowork_agent.allowed_tools},
            requested_token_cost=10,
            tokens_used_so_far=0,
            live_state=authority.live_state_checker(ORG),
        )

    assert _check().is_valid  # live mid-session

    repo.add_grant(
        org_id=ORG,
        principal_id=DEVON,
        grant=_grant(LLM, GrantSource.EXPLICIT_DENY, "offboarding, ticket SEC-202"),
    )
    await authority.invalidate_principal_authority(org_id=ORG, principal_id=DEVON)

    result = _check()
    assert not result.is_valid
    assert result.failed_stage is ValidationStage.REVOCATION


async def test_start_and_refresh_apply_the_same_gate() -> None:
    """A session that starts must clear exactly the gate a session that continues
    does -- otherwise the first token would be privileged."""
    session, _, _ = _session(grants=[_grant(LLM)])  # no memory.search granted
    started = await session.start(org_id=ORG, principal_id=DEVON)
    refreshed = await session.refresh(org_id=ORG, principal_id=DEVON)
    assert set(started.scope) == set(refreshed.scope) == {LLM}


async def test_unknown_principal_cannot_open_a_session() -> None:
    from skylize.app.principal.errors import PrincipalNotFound

    session, _, _ = _session(grants=[_grant(LLM)])
    with pytest.raises(PrincipalNotFound):
        await session.start(org_id=ORG, principal_id="ghost")
