"""Mint-time attenuation of `authority_level`.

The per-employee shape's security property has two axes, not one:

    an employee's agent can never do anything the employee could not do himself.

`test_mint_principal_gate.py` covers the SCOPE axis (which tools). This file
covers the LEVEL axis (how far up the approval ladder), which is what the inline
decision evaluator's `authority_check` stage reads when it decides whether an
action needs a human at all.

Before this change `GovernanceAuthority.mint` signed `contract.authority_level`
unconditionally, so an executive-level contract driven by a worker minted an
executive token, and `Principal.authority_level` was a column nothing in `src/`
ever read. The rule now is

    token.authority_level = min(agent_contract_level, principal_level)

over `contracts.base.AUTHORITY_RANK` (worker=1 .. executive=5), applied ONLY when
`on_behalf_of_principal` is present.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from skylize.app.audit.service import AuditService
from skylize.app.governance import GovernanceAuthority
from skylize.app.principal.authority import attenuate_level, compile_authority
from skylize.app.principal.models import Grant, GrantSource, Principal
from skylize.app.principal.provider import (
    InMemoryPrincipalRepository,
    PrincipalAuthorityService,
)
from skylize.config import Settings
from skylize.contracts.base import AUTHORITY_RANK
from skylize.contracts.registry import MVP_REGISTRY
from skylize.contracts.token import verify_token_signature
from skylize.dal.memory import InMemoryAuditRepository, InMemoryGovernanceRepository
from skylize.events.memory_bus import InMemoryEventBus

ORG = "org_test"
PRINCIPAL = "devon"

#: An executive-level contract. Deliberately NOT `cfo_agent` — that is one of the
#: five stateless agents, which this pass must not touch in any way.
EXEC_AGENT = "ceo"  # executive; llm.generate, memory.search, orchestrator.delegate
WORKER_AGENT = "hook_generator_agent"  # worker; llm.generate, memory.search

_ALL_LEVELS = ("worker", "manager", "director", "vp", "executive")


def _principal(level: str, **kw) -> Principal:
    base = dict(
        principal_id=PRINCIPAL,
        org_id=ORG,
        display_name="Devon",
        authority_level=level,
    )
    return Principal(**{**base, **kw})


def _grant(scope: str) -> Grant:
    return Grant(
        scope=scope,
        source=GrantSource.POSITION,
        valid_from=datetime.now(timezone.utc) - timedelta(days=1),
    )


def _authority(*, principal: Principal | None = None, grants: tuple[Grant, ...] = ()):
    """An Authority wired to an in-memory principal store. Returns the audit repo
    too, because a clamp that leaves no trail is a clamp nobody can audit."""
    bus = InMemoryEventBus()
    audit_repo = InMemoryAuditRepository()
    audit = AuditService(bus, audit_repo)

    repo = InMemoryPrincipalRepository()
    if principal is not None:
        repo.add_principal(principal)
        for g in grants:
            repo.add_grant(org_id=ORG, principal_id=principal.principal_id, grant=g)

    authority = GovernanceAuthority.build(
        repo=InMemoryGovernanceRepository(),
        audit=audit,
        bus=bus,
        registry=MVP_REGISTRY,
        settings=Settings(backend="memory"),
        principal_authority=PrincipalAuthorityService(repo),
    )
    return authority, audit_repo


def _grants_for(agent_id: str) -> tuple[Grant, ...]:
    """Grant the human every tool the contract allows, so SCOPE never denies and
    only the LEVEL axis is under test."""
    return tuple(_grant(t.tool_id) for t in MVP_REGISTRY.resolve(agent_id).allowed_tools)


# --------------------------------------------------------------------------- #
# The pure kernel
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("agent_level", _ALL_LEVELS)
@pytest.mark.parametrize("principal_level", _ALL_LEVELS)
def test_attenuate_level_is_min_over_the_canonical_ladder(
    agent_level: str, principal_level: str
) -> None:
    """All 25 pairs. The result's RANK is the min of the two input ranks, and the
    result is always one of the two inputs -- never a synthesized third level."""
    got = attenuate_level(agent_level=agent_level, principal_level=principal_level)
    assert AUTHORITY_RANK[got] == min(
        AUTHORITY_RANK[agent_level], AUTHORITY_RANK[principal_level]
    )
    assert got in (agent_level, principal_level)


def test_attenuate_level_is_symmetric() -> None:
    """min() not max(), and order of arguments cannot change the answer. A
    max()-shaped bug would be invisible in the agent>=principal direction alone."""
    for a in _ALL_LEVELS:
        for p in _ALL_LEVELS:
            assert attenuate_level(agent_level=a, principal_level=p) == attenuate_level(
                agent_level=p, principal_level=a
            )


def test_the_ladder_the_kernel_uses_is_the_evaluator_ladder() -> None:
    """One ladder, not two. If the evaluator ever re-declares its own `_RANK`,
    `attenuate_level`'s min() and the evaluator's `<` could disagree about which
    of two levels is higher, and the clamp would stop meaning what it says."""
    from skylize.app.decision_engine.evaluator import _RANK

    assert _RANK is AUTHORITY_RANK
    assert AUTHORITY_RANK == {
        "worker": 1, "manager": 2, "director": 3, "vp": 4, "executive": 5
    }


# --------------------------------------------------------------------------- #
# Mint-time: the clamp
# --------------------------------------------------------------------------- #


async def test_executive_agent_for_a_worker_mints_a_worker_token() -> None:
    """THE case this pass exists for. An executive-level contract driven by a
    worker must not hand that worker executive authority."""
    authority, _ = _authority(
        principal=_principal("worker"), grants=_grants_for(EXEC_AGENT)
    )
    contract = MVP_REGISTRY.resolve(EXEC_AGENT)
    assert contract.authority_level == "executive"  # guard: the premise holds

    token = await authority.mint(
        contract, org_id=ORG, correlation_id=uuid4(), on_behalf_of_principal=PRINCIPAL
    )

    assert token.authority_level == "worker"
    assert token.token_version == "1.1"
    # The clamped level is inside the signature, not decoration on top of it.
    assert verify_token_signature(token, authority.public_key) is True


async def test_worker_agent_for_an_executive_still_mints_a_worker_token() -> None:
    """min(), not max(). A senior human does not promote the agent they drive:
    the contract's own ceiling still binds. This is the direction a max()-shaped
    bug would pass the previous test and fail here."""
    authority, _ = _authority(
        principal=_principal("executive"), grants=_grants_for(WORKER_AGENT)
    )
    contract = MVP_REGISTRY.resolve(WORKER_AGENT)
    assert contract.authority_level == "worker"

    token = await authority.mint(
        contract, org_id=ORG, correlation_id=uuid4(), on_behalf_of_principal=PRINCIPAL
    )

    assert token.authority_level == "worker"
    assert verify_token_signature(token, authority.public_key) is True


@pytest.mark.parametrize(
    "principal_level,expected",
    [
        ("worker", "worker"),
        ("manager", "manager"),
        ("director", "director"),
        ("vp", "vp"),
        ("executive", "executive"),  # equal ranks -> unchanged, no spurious clamp
    ],
)
async def test_every_rung_clamps_an_executive_contract_to_itself(
    principal_level: str, expected: str
) -> None:
    """Walks the whole ladder against a fixed executive contract, including the
    `vp` rung -- the level the L1-L5 doc ladder never had a row for."""
    authority, _ = _authority(
        principal=_principal(principal_level), grants=_grants_for(EXEC_AGENT)
    )
    token = await authority.mint(
        MVP_REGISTRY.resolve(EXEC_AGENT),
        org_id=ORG,
        correlation_id=uuid4(),
        on_behalf_of_principal=PRINCIPAL,
    )
    assert token.authority_level == expected


async def test_equal_levels_are_not_reported_as_an_attenuation() -> None:
    """No clamp happened, so no `governance.authority_attenuated` row. An audit
    trail that logs a narrowing on every mint teaches readers to ignore it."""
    authority, audit_repo = _authority(
        principal=_principal("executive"), grants=_grants_for(EXEC_AGENT)
    )
    await authority.mint(
        MVP_REGISTRY.resolve(EXEC_AGENT),
        org_id=ORG,
        correlation_id=uuid4(),
        on_behalf_of_principal=PRINCIPAL,
    )
    assert not _attenuation_rows(audit_repo)


# --------------------------------------------------------------------------- #
# The autonomous path is untouched
# --------------------------------------------------------------------------- #


async def test_autonomous_mint_keeps_the_contract_level_unclamped() -> None:
    """No principal means no human to clamp against, so the agent's own level --
    rooted at `human_owner` and flowed down the agent org tree -- stands. A
    high-authority autonomous agent must not be silently demoted to worker."""
    bus = InMemoryEventBus()
    audit_repo = InMemoryAuditRepository()
    authority = GovernanceAuthority.build(
        repo=InMemoryGovernanceRepository(),
        audit=AuditService(bus, audit_repo),
        bus=bus,
        registry=MVP_REGISTRY,
        settings=Settings(backend="memory"),
        principal_authority=None,  # not even wired -- the autonomous shape needs none
    )
    contract = MVP_REGISTRY.resolve(EXEC_AGENT)

    token = await authority.mint(contract, org_id=ORG, correlation_id=uuid4())

    assert token.authority_level == "executive"
    assert token.token_version == "1.0"
    assert token.on_behalf_of is None
    assert not _attenuation_rows(audit_repo)


async def test_autonomous_mint_is_unclamped_even_when_a_principal_exists() -> None:
    """A worker principal in the store does NOT bleed into an autonomous mint.
    The clamp keys off the `on_behalf_of_principal` argument, never off whatever
    rows happen to exist for the tenant."""
    authority, _ = _authority(
        principal=_principal("worker"), grants=_grants_for(EXEC_AGENT)
    )
    token = await authority.mint(
        MVP_REGISTRY.resolve(EXEC_AGENT), org_id=ORG, correlation_id=uuid4()
    )
    assert token.authority_level == "executive"
    assert token.token_version == "1.0"


# --------------------------------------------------------------------------- #
# The live cowork path
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("principal_level", _ALL_LEVELS)
async def test_the_live_cowork_path_is_unaffected_at_every_rung(
    principal_level: str,
) -> None:
    """`cowork_agent` is the only contract any live request path mints on behalf
    of a principal (edge/routes/cowork.py), and it is already `worker` -- the
    floor of the ladder. min(worker, anything) == worker, so this change cannot
    alter a single token that path issues today."""
    from skylize.edge.routes.cowork import COWORK_AGENT_ID

    contract = MVP_REGISTRY.resolve(COWORK_AGENT_ID)
    assert contract.authority_level == "worker"

    authority, audit_repo = _authority(
        principal=_principal(principal_level), grants=_grants_for(COWORK_AGENT_ID)
    )
    token = await authority.mint(
        contract, org_id=ORG, correlation_id=uuid4(), on_behalf_of_principal=PRINCIPAL
    )

    assert token.authority_level == "worker"
    assert not _attenuation_rows(audit_repo)  # already at the floor: nothing to clamp


# --------------------------------------------------------------------------- #
# The clamp is audited
# --------------------------------------------------------------------------- #


def _attenuation_rows(audit_repo: InMemoryAuditRepository) -> list[object]:
    return [
        r
        for r in audit_repo.rows
        if getattr(r, "action_type", None) == "governance.authority_attenuated"
    ]


async def test_a_real_clamp_writes_an_audit_row_naming_both_levels() -> None:
    """Scope excess raises and level excess clamps; the clamp is the quiet one,
    so it has to leave a record. An auditor must be able to answer 'why did this
    executive contract produce a worker token?' without re-deriving it."""
    authority, audit_repo = _authority(
        principal=_principal("worker"), grants=_grants_for(EXEC_AGENT)
    )
    await authority.mint(
        MVP_REGISTRY.resolve(EXEC_AGENT),
        org_id=ORG,
        correlation_id=uuid4(),
        on_behalf_of_principal=PRINCIPAL,
    )

    rows = _attenuation_rows(audit_repo)
    assert len(rows) == 1
    reason = rows[0].result_reason or ""
    assert "executive" in reason and "worker" in reason and PRINCIPAL in reason
    # The row carries the level actually issued, not the one the contract asked for.
    assert rows[0].authority_level == "worker"


async def test_the_issued_token_row_and_event_carry_the_clamped_level() -> None:
    """The clamp must not stop at the signature. The persisted token row, the
    `GovernanceTokenIssued` event and the `token_issued` audit row all read off
    the signed token, so all three must show `worker`, not `executive` -- these
    are what a revocation sweep and a compliance export actually read."""
    bus = InMemoryEventBus()
    audit_repo = InMemoryAuditRepository()
    gov_repo = InMemoryGovernanceRepository()
    repo = InMemoryPrincipalRepository()
    repo.add_principal(_principal("worker"))
    for g in _grants_for(EXEC_AGENT):
        repo.add_grant(org_id=ORG, principal_id=PRINCIPAL, grant=g)

    authority = GovernanceAuthority.build(
        repo=gov_repo,
        audit=AuditService(bus, audit_repo),
        bus=bus,
        registry=MVP_REGISTRY,
        settings=Settings(backend="memory"),
        principal_authority=PrincipalAuthorityService(repo),
    )
    token = await authority.mint(
        MVP_REGISTRY.resolve(EXEC_AGENT),
        org_id=ORG,
        correlation_id=uuid4(),
        on_behalf_of_principal=PRINCIPAL,
    )

    assert token.authority_level == "worker"

    # 1. the persisted token row -- what a revocation sweep reads
    assert gov_repo._tokens[token.token_id].authority_level == "worker"

    # 2. the emitted GovernanceTokenIssued event, header and payload alike
    emitted = [
        e
        for e in bus._published
        if type(e).__name__ == "GovernanceTokenIssued"
        and e.payload.token_id == token.token_id
    ]
    assert len(emitted) == 1
    assert emitted[0].authority_level == "worker"
    assert emitted[0].payload.authority_level == "worker"

    # 3. the token_issued audit row -- what a compliance export reads
    issued = [
        r
        for r in audit_repo.rows
        if getattr(r, "action_type", None) == "governance.token_issued"
    ]
    assert len(issued) == 1
    assert issued[0].authority_level == "worker"


# --------------------------------------------------------------------------- #
# The fingerprint covers the level
# --------------------------------------------------------------------------- #


def test_a_demotion_that_touches_no_grant_still_changes_the_fingerprint() -> None:
    """Without this the clamp would hold only until the next mint. A demoted
    human's outstanding token carries the level they just lost; the only thing
    that revokes it before expiry is `assert_snapshot_current` seeing a changed
    fingerprint -- and a demotion changes no scope at all."""
    at = datetime.now(timezone.utc)
    grants = [_grant("llm.generate"), _grant("memory.search")]

    before = compile_authority(_principal("executive"), grants, at=at)
    after = compile_authority(_principal("worker"), grants, at=at)

    assert before.scopes == after.scopes  # nothing about the grants changed
    assert before.fingerprint != after.fingerprint


def test_the_snapshot_carries_the_principals_level() -> None:
    """`mint` clamps with this field. It comes off the `Principal` row that
    `snapshot_for` already loaded, which is what keeps the clamp at zero extra
    queries and exactly one principal read per mint."""
    snap = compile_authority(
        _principal("director"), [_grant("llm.generate")], at=datetime.now(timezone.utc)
    )
    assert snap.authority_level == "director"


def test_a_level_cannot_be_forged_by_naming_a_scope_after_it() -> None:
    """The level is appended after the sorted scopes behind its own separator, so
    a principal granted a scope literally called "executive" cannot collide with
    a principal whose LEVEL is executive."""
    at = datetime.now(timezone.utc)
    scope_named_like_a_level = compile_authority(
        _principal("worker"), [_grant("executive")], at=at
    )
    actually_executive = compile_authority(_principal("executive"), [], at=at)
    assert scope_named_like_a_level.fingerprint != actually_executive.fingerprint
