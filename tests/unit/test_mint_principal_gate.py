"""The mint-time principal gate.

The security property the per-employee shape exists to guarantee:

    an employee's agent can never do anything the employee could not do himself.

`GovernanceAuthority.mint` enforces it by intersecting the requested scope with
the principal's COMPILED authority before anything is signed. These tests prove
the gate holds, that it denies loudly rather than trimming quietly, that every
denial is audited, and that the autonomous path is completely untouched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from skylize.app.audit.service import AuditService
from skylize.app.governance import GovernanceAuthority
from skylize.app.principal.errors import (
    AuthorityExceeded,
    AuthorityUnavailable,
    PrincipalNotFound,
    PrincipalSuspended,
)
from skylize.app.principal.models import Grant, GrantSource, Principal
from skylize.app.principal.provider import (
    InMemoryPrincipalRepository,
    PrincipalAuthorityService,
)
from skylize.config import Settings
from skylize.contracts.registry import MVP_REGISTRY
from skylize.contracts.token import verify_token_signature
from skylize.dal.memory import InMemoryAuditRepository, InMemoryGovernanceRepository
from skylize.events.memory_bus import InMemoryEventBus

ORG = "org_test"
PRINCIPAL = "devon"
AGENT = "hook_generator_agent"  # contract allows llm.generate + memory.search


def _principal(**kw) -> Principal:
    base = dict(
        principal_id=PRINCIPAL,
        org_id=ORG,
        display_name="Devon",
        authority_level="manager",
    )
    return Principal(**{**base, **kw})


def _grant(scope: str) -> Grant:
    return Grant(
        scope=scope,
        source=GrantSource.POSITION,
        valid_from=datetime.now(timezone.utc) - timedelta(days=1),
    )


def _authority(*, principal=None, grants=(), with_provider=True):
    bus = InMemoryEventBus()
    audit_repo = InMemoryAuditRepository()
    audit = AuditService(bus, audit_repo)

    provider = None
    if with_provider:
        repo = InMemoryPrincipalRepository()
        if principal is not None:
            repo.add_principal(principal)
            for g in grants:
                repo.add_grant(org_id=ORG, principal_id=principal.principal_id, grant=g)
        provider = PrincipalAuthorityService(repo)

    authority = GovernanceAuthority.build(
        repo=InMemoryGovernanceRepository(),
        audit=audit,
        bus=bus,
        registry=MVP_REGISTRY,
        settings=Settings(backend="memory"),
        principal_authority=provider,
    )
    return authority, bus, audit_repo


# --------------------------------------------------------------------------- #
# The autonomous path is untouched
# --------------------------------------------------------------------------- #


async def test_autonomous_mint_is_unchanged_and_v10() -> None:
    """No principal requested -> the classic v1.0 token, no claim, no provider
    consulted. This is the shape every pre-existing call site mints."""
    authority, _, _ = _authority(with_provider=False)
    contract = MVP_REGISTRY.resolve(AGENT)
    token = await authority.mint(contract, org_id=ORG, correlation_id=uuid4())

    assert token.token_version == "1.0"
    assert token.on_behalf_of is None
    assert verify_token_signature(token, authority.public_key) is True


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


async def test_mint_on_behalf_of_binds_a_v11_claim() -> None:
    authority, _, _ = _authority(
        principal=_principal(), grants=[_grant("llm.generate"), _grant("memory.search")]
    )
    contract = MVP_REGISTRY.resolve(AGENT)
    token = await authority.mint(
        contract, org_id=ORG, correlation_id=uuid4(), on_behalf_of_principal=PRINCIPAL
    )

    assert token.token_version == "1.1"
    assert token.on_behalf_of is not None
    assert token.on_behalf_of.principal_id == PRINCIPAL
    assert token.on_behalf_of.session_kind == "cowork"
    assert len(token.on_behalf_of.authority_fingerprint) == 64  # sha256 hex
    assert verify_token_signature(token, authority.public_key) is True


async def test_mint_refuses_scope_the_principal_does_not_have() -> None:
    """THE security property. The contract allows memory.search, but this human
    was never granted it, so the agent cannot be handed it."""
    authority, _, _ = _authority(
        principal=_principal(), grants=[_grant("llm.generate")]  # no memory.search
    )
    contract = MVP_REGISTRY.resolve(AGENT)

    with pytest.raises(AuthorityExceeded) as ei:
        await authority.mint(
            contract,
            org_id=ORG,
            correlation_id=uuid4(),
            on_behalf_of_principal=PRINCIPAL,
        )
    assert ei.value.excess == ["memory.search"]
    assert ei.value.failed_stage == "scope"


async def test_denial_is_loud_not_a_silent_trim() -> None:
    """An over-broad request must raise, never quietly return a narrower token.
    A silently-narrowed token is the failure mode where an agent does less than
    it was asked and nobody notices."""
    authority, _, _ = _authority(
        principal=_principal(), grants=[_grant("llm.generate")]
    )
    contract = MVP_REGISTRY.resolve(AGENT)
    with pytest.raises(AuthorityExceeded):
        await authority.mint(
            contract,
            org_id=ORG,
            correlation_id=uuid4(),
            on_behalf_of_principal=PRINCIPAL,
        )


async def test_narrowed_request_within_authority_succeeds() -> None:
    """Asking for only what the human has is fine -- the gate narrows, it does
    not forbid the per-employee shape outright."""
    authority, _, _ = _authority(
        principal=_principal(), grants=[_grant("llm.generate")]
    )
    contract = MVP_REGISTRY.resolve(AGENT)
    token = await authority.mint(
        contract,
        org_id=ORG,
        correlation_id=uuid4(),
        scope=["llm.generate"],
        on_behalf_of_principal=PRINCIPAL,
    )
    assert token.scope == ["llm.generate"]
    assert token.token_version == "1.1"


async def test_explicit_deny_beats_the_position_grant_at_mint() -> None:
    authority, _, _ = _authority(
        principal=_principal(),
        grants=[
            _grant("llm.generate"),
            Grant(
                scope="llm.generate",
                source=GrantSource.EXPLICIT_DENY,
                valid_from=datetime.now(timezone.utc) - timedelta(days=1),
                justification="SoD exception, ticket SEC-114",
            ),
        ],
    )
    contract = MVP_REGISTRY.resolve(AGENT)
    with pytest.raises(AuthorityExceeded):
        await authority.mint(
            contract,
            org_id=ORG,
            correlation_id=uuid4(),
            scope=["llm.generate"],
            on_behalf_of_principal=PRINCIPAL,
        )


# --------------------------------------------------------------------------- #
# Fail-closed on every "cannot establish authority" path
# --------------------------------------------------------------------------- #


async def test_unknown_principal_fails_closed() -> None:
    authority, _, _ = _authority(principal=None)  # provider wired, no principal
    contract = MVP_REGISTRY.resolve(AGENT)
    with pytest.raises(PrincipalNotFound):
        await authority.mint(
            contract, org_id=ORG, correlation_id=uuid4(), on_behalf_of_principal="ghost"
        )


async def test_suspended_principal_fails_closed() -> None:
    authority, _, _ = _authority(
        principal=_principal(suspended_at=datetime.now(timezone.utc)),
        grants=[_grant("llm.generate")],
    )
    contract = MVP_REGISTRY.resolve(AGENT)
    with pytest.raises(PrincipalSuspended):
        await authority.mint(
            contract,
            org_id=ORG,
            correlation_id=uuid4(),
            scope=["llm.generate"],
            on_behalf_of_principal=PRINCIPAL,
        )


async def test_missing_provider_fails_closed_rather_than_issuing_ungated() -> None:
    """If the Authority cannot check the human's authority at all, it must refuse
    -- not issue a principal-bound token that nothing gated."""
    authority, _, _ = _authority(with_provider=False)
    contract = MVP_REGISTRY.resolve(AGENT)
    with pytest.raises(AuthorityUnavailable):
        await authority.mint(
            contract,
            org_id=ORG,
            correlation_id=uuid4(),
            on_behalf_of_principal=PRINCIPAL,
        )


async def test_principal_from_another_org_is_invisible() -> None:
    """Org scoping at the mint gate -- the durable side enforces the same thing
    with RLS."""
    authority, _, _ = _authority(
        principal=_principal(), grants=[_grant("llm.generate")]
    )
    contract = MVP_REGISTRY.resolve(AGENT)
    with pytest.raises(PrincipalNotFound):
        await authority.mint(
            contract,
            org_id="some_other_org",
            correlation_id=uuid4(),
            scope=["llm.generate"],
            on_behalf_of_principal=PRINCIPAL,
        )


# --------------------------------------------------------------------------- #
# Denials are auditable
# --------------------------------------------------------------------------- #


async def test_denial_is_audited_before_it_propagates() -> None:
    authority, _, audit_repo = _authority(
        principal=_principal(), grants=[_grant("llm.generate")]
    )
    contract = MVP_REGISTRY.resolve(AGENT)
    with pytest.raises(AuthorityExceeded):
        await authority.mint(
            contract,
            org_id=ORG,
            correlation_id=uuid4(),
            on_behalf_of_principal=PRINCIPAL,
        )

    denials = [
        r for r in audit_repo.rows
        if r.action_type == "governance.principal_scope_denied"
    ]
    assert len(denials) == 1
    assert denials[0].result == "denied"
    assert "AuthorityExceeded" in denials[0].result_reason
    assert "memory.search" in denials[0].result_reason


async def test_no_token_is_persisted_or_emitted_on_denial() -> None:
    """A refused mint must leave no trace of an issued token -- the deny happens
    before signing, persistence, and emission."""
    authority, bus, _ = _authority(
        principal=_principal(), grants=[_grant("llm.generate")]
    )
    contract = MVP_REGISTRY.resolve(AGENT)
    with pytest.raises(AuthorityExceeded):
        await authority.mint(
            contract,
            org_id=ORG,
            correlation_id=uuid4(),
            on_behalf_of_principal=PRINCIPAL,
        )
    assert not bus.published_of_type("governance.token_issued")
