"""Authority freshness at verification time.

A signed token is a COPY of an authorization decision. If the human's grants
change after mint, every live token minted under the old authority must stop
working at its next call -- not at token expiry. That is what this check does.

WHY IT IS AN IN-MEMORY SNAPSHOT AND NOT A REDIS READ: `validate_tool_call` is a
SYNCHRONOUS function (contracts/token.py), called from the hot tool-dispatch
path, so it cannot await a Redis or Postgres round trip. The codebase already
solves exactly this problem for token revocation and the kill switch:
`GovernanceSnapshot` is the O(1) in-memory cache, `GovernanceBroadcast` fans
invalidations to every instance, and `rehydrate()` warms it at startup. The
authority fingerprint reuses that machinery rather than inventing a second one.

The consequence is a STRONGER fail-closed posture than a read-through cache: a
miss cannot fall back to the database from a sync frame, so a miss DENIES.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from skylize.app.audit.service import AuditService
from skylize.app.governance import GovernanceAuthority
from skylize.app.governance.broadcast import InMemoryGovernanceBroadcast
from skylize.app.principal.models import Grant, GrantSource, Principal
from skylize.app.principal.provider import (
    InMemoryPrincipalRepository,
    PrincipalAuthorityService,
)
from skylize.config import Settings
from skylize.contracts.registry import MVP_REGISTRY
from skylize.contracts.token import (
    AllowAllLiveState,
    ValidationStage,
    validate_tool_call,
)
from skylize.dal.memory import InMemoryAuditRepository, InMemoryGovernanceRepository
from skylize.events.memory_bus import InMemoryEventBus

ORG = "org_test"
PRINCIPAL = "devon"
AGENT = "hook_generator_agent"
TOOL = "llm.generate"


def _principal() -> Principal:
    return Principal(
        principal_id=PRINCIPAL, org_id=ORG, display_name="Devon", authority_level="manager"
    )


def _grant(scope: str, source=GrantSource.POSITION, justification=None) -> Grant:
    return Grant(
        scope=scope,
        source=source,
        valid_from=datetime.now(timezone.utc) - timedelta(days=1),
        justification=justification,
    )


def _build(*, grants, broadcast=None):
    """An Authority whose principal repo is mutable, so a grant can be revoked
    mid-test exactly as it would be in production."""
    bus = InMemoryEventBus()
    audit = AuditService(bus, InMemoryAuditRepository())
    repo = InMemoryPrincipalRepository()
    repo.add_principal(_principal())
    for g in grants:
        repo.add_grant(org_id=ORG, principal_id=PRINCIPAL, grant=g)
    authority = GovernanceAuthority.build(
        repo=InMemoryGovernanceRepository(),
        audit=audit,
        bus=bus,
        registry=MVP_REGISTRY,
        settings=Settings(backend="memory"),
        principal_authority=PrincipalAuthorityService(repo),
        broadcast=broadcast,
    )
    return authority, repo


def _validate(authority, token):
    contract = MVP_REGISTRY.resolve(AGENT)
    return validate_tool_call(
        token=token,
        public_key=authority.public_key,
        requested_tool_id=TOOL,
        contract_allowed_tool_ids={t.tool_id for t in contract.allowed_tools},
        requested_token_cost=10,
        tokens_used_so_far=0,
        live_state=authority.live_state_checker(ORG),
    )


async def _mint(authority):
    return await authority.mint(
        MVP_REGISTRY.resolve(AGENT),
        org_id=ORG,
        correlation_id=uuid4(),
        scope=[TOOL],
        on_behalf_of_principal=PRINCIPAL,
    )


# --------------------------------------------------------------------------- #
# Happy path, then revocation
# --------------------------------------------------------------------------- #


async def test_fresh_principal_token_validates() -> None:
    authority, _ = _build(grants=[_grant(TOOL)])
    token = await _mint(authority)
    result = _validate(authority, token)
    assert result.is_valid, result.reason


async def test_revoked_grant_invalidates_a_live_token_at_the_next_call() -> None:
    """THE revocation property. The token is still unexpired and its signature is
    still perfectly valid -- what changed is the human behind it."""
    authority, repo = _build(grants=[_grant(TOOL)])
    token = await _mint(authority)
    assert _validate(authority, token).is_valid  # live before the revocation

    # The human loses the scope, and the grant-write path invalidates.
    repo.add_grant(
        org_id=ORG,
        principal_id=PRINCIPAL,
        grant=_grant(TOOL, GrantSource.EXPLICIT_DENY, "offboarding, ticket SEC-200"),
    )
    await authority.invalidate_principal_authority(org_id=ORG, principal_id=PRINCIPAL)

    result = _validate(authority, token)
    assert not result.is_valid
    assert result.failed_stage is ValidationStage.REVOCATION
    assert "authority" in result.reason.lower()


async def test_revocation_propagates_to_another_instance() -> None:
    """Two Authorities sharing one broadcast, as two API replicas would. The
    instance that did NOT handle the grant write must also refuse."""
    shared = InMemoryGovernanceBroadcast()
    a, _ = _build(grants=[_grant(TOOL)], broadcast=shared)
    b, _ = _build(grants=[_grant(TOOL)], broadcast=shared)
    await b.start_subscriber()

    token = await a.mint(
        MVP_REGISTRY.resolve(AGENT),
        org_id=ORG,
        correlation_id=uuid4(),
        scope=[TOOL],
        on_behalf_of_principal=PRINCIPAL,
    )
    # Teach instance B the same authority, as its own mint would have. Each test
    # Authority generates its own ephemeral signing key, so the signature is
    # always checked against A's public key -- what varies between the two
    # instances here is only the SNAPSHOT, which is what this test is about.
    b._snapshot.set_authority_fingerprint(
        ORG, PRINCIPAL, token.on_behalf_of.authority_fingerprint
    )
    contract = MVP_REGISTRY.resolve(AGENT)

    def _on_instance_b():
        return validate_tool_call(
            token=token,
            public_key=a.public_key,
            requested_tool_id=TOOL,
            contract_allowed_tool_ids={t.tool_id for t in contract.allowed_tools},
            requested_token_cost=10,
            tokens_used_so_far=0,
            live_state=b.live_state_checker(ORG),
        )

    assert _on_instance_b().is_valid

    await a.invalidate_principal_authority(org_id=ORG, principal_id=PRINCIPAL)

    result = _on_instance_b()
    assert not result.is_valid
    assert result.failed_stage is ValidationStage.REVOCATION


# --------------------------------------------------------------------------- #
# Fail-closed
# --------------------------------------------------------------------------- #


async def test_cache_miss_denies_rather_than_allowing() -> None:
    """A cold instance -- restarted, or one that never minted this token -- has
    no entry. It must refuse, because it cannot confirm the authority from a
    synchronous frame. This is the "flush the cache" case."""
    authority, _ = _build(grants=[_grant(TOOL)])
    token = await _mint(authority)
    assert _validate(authority, token).is_valid

    authority._snapshot.forget_authority(ORG, PRINCIPAL)  # simulate a flush/restart

    result = _validate(authority, token)
    assert not result.is_valid
    assert result.failed_stage is ValidationStage.REVOCATION
    assert "not established" in result.reason


async def test_checker_without_freshness_support_denies_a_principal_token() -> None:
    """AllowAllLiveState cannot evaluate freshness, so it must not be taken as
    asserting the authority is fresh."""
    authority, _ = _build(grants=[_grant(TOOL)])
    token = await _mint(authority)
    contract = MVP_REGISTRY.resolve(AGENT)

    result = validate_tool_call(
        token=token,
        public_key=authority.public_key,
        requested_tool_id=TOOL,
        contract_allowed_tool_ids={t.tool_id for t in contract.allowed_tools},
        requested_token_cost=10,
        tokens_used_so_far=0,
        live_state=AllowAllLiveState(),
    )
    assert not result.is_valid
    assert result.failed_stage is ValidationStage.REVOCATION
    assert "cannot verify authority freshness" in result.reason


async def test_another_orgs_checker_cannot_satisfy_the_claim() -> None:
    """The org is taken from the CHECKER's binding, never from the token, so a
    token cannot aim the lookup at a tenant where its fingerprint happens to
    match."""
    authority, _ = _build(grants=[_grant(TOOL)])
    token = await _mint(authority)
    contract = MVP_REGISTRY.resolve(AGENT)

    result = validate_tool_call(
        token=token,
        public_key=authority.public_key,
        requested_tool_id=TOOL,
        contract_allowed_tool_ids={t.tool_id for t in contract.allowed_tools},
        requested_token_cost=10,
        tokens_used_so_far=0,
        live_state=authority.live_state_checker("some_other_org"),
    )
    assert not result.is_valid
    assert result.failed_stage is ValidationStage.REVOCATION


# --------------------------------------------------------------------------- #
# The autonomous path is untouched
# --------------------------------------------------------------------------- #


async def test_autonomous_token_skips_the_freshness_check_entirely() -> None:
    """No claim -> the new code path is never entered, which is why every
    pre-existing caller and duck-typed test checker is unaffected."""
    authority, _ = _build(grants=[_grant(TOOL)])
    token = await authority.mint(
        MVP_REGISTRY.resolve(AGENT), org_id=ORG, correlation_id=uuid4(), scope=[TOOL]
    )
    assert token.on_behalf_of is None

    contract = MVP_REGISTRY.resolve(AGENT)
    result = validate_tool_call(
        token=token,
        public_key=authority.public_key,
        requested_tool_id=TOOL,
        contract_allowed_tool_ids={t.tool_id for t in contract.allowed_tools},
        requested_token_cost=10,
        tokens_used_so_far=0,
        live_state=AllowAllLiveState(),  # no freshness support, and none needed
    )
    assert result.is_valid, result.reason


@pytest.mark.parametrize("stage_order_probe", [ValidationStage.REVOCATION])
def test_validation_stage_enum_is_unchanged(stage_order_probe) -> None:
    """The freshness check reuses REVOCATION rather than adding a stage, so the
    canonical ordered pipeline is exactly the six stages it always was."""
    assert [s.value for s in ValidationStage] == [
        "signature",
        "expiry",
        "revocation",
        "scope",
        "budget",
        "delegation",
    ]
