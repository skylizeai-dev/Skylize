"""
Proves the security property, not just the happy path.

The invariant under test:
    effective ⊆ contract ∩ principal ∩ parent, for ALL inputs.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from skylize.app.principal.authority import (
    assert_snapshot_current,
    attenuate_for_subagent,
    compile_authority,
    resolve_effective_scope,
)
from skylize.app.principal.errors import (
    AuthorityExceeded,
    CeilingExceeded,
    EnvelopeNotFound,
    ExpiryExtensionDenied,
    PrincipalSuspended,
    StaleAuthority,
)
from skylize.app.principal.journal import assemble_brief
from skylize.app.principal.models import (
    ActorKind,
    Grant,
    GrantSource,
    JournalEntry,
    OnBehalfOf,
    Principal,
    SpendEnvelope,
)
from skylize.app.principal.spend import SpendLedger

T0 = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
UNIVERSE = ["llm.generate", "memory.search", "stripe.refund", "slack.post"]


def _principal(**kw) -> Principal:
    base = dict(
        principal_id="devon",
        org_id="org_1",
        display_name="Devon",
        position_id="marketing_lead",
        authority_level="manager",
    )
    return Principal(**{**base, **kw})


def _g(scope, source=GrantSource.POSITION, **kw) -> Grant:
    just = kw.pop("justification", None)
    if source in (GrantSource.EXPLICIT_GRANT, GrantSource.EXPLICIT_DENY):
        just = just or "SoD exception, ticket SEC-114"
    return Grant(
        scope=scope,
        source=source,
        valid_from=kw.pop("valid_from", T0 - timedelta(days=1)),
        valid_to=kw.pop("valid_to", None),
        justification=just,
    )


# --------------------------------------------------------------------------- #
# compile_authority
# --------------------------------------------------------------------------- #


def test_explicit_deny_beats_position_grant() -> None:
    snap = compile_authority(
        _principal(),
        [_g("stripe.refund"), _g("stripe.refund", GrantSource.EXPLICIT_DENY)],
        at=T0,
    )
    assert "stripe.refund" not in snap.scopes


def test_expired_grant_is_inert() -> None:
    snap = compile_authority(
        _principal(),
        [_g("slack.post", valid_to=T0 - timedelta(hours=1))],
        at=T0,
    )
    assert snap.scopes == frozenset()


def test_suspended_principal_fails_closed() -> None:
    with pytest.raises(PrincipalSuspended):
        compile_authority(_principal(suspended_at=T0), [_g("llm.generate")], at=T0)


def test_fingerprint_changes_when_authority_changes() -> None:
    a = compile_authority(_principal(), [_g("llm.generate")], at=T0)
    b = compile_authority(
        _principal(), [_g("llm.generate"), _g("stripe.refund")], at=T0
    )
    assert a.fingerprint != b.fingerprint


def test_fingerprint_is_order_independent() -> None:
    a = compile_authority(_principal(), [_g("a.x"), _g("b.y")], at=T0)
    b = compile_authority(_principal(), [_g("b.y"), _g("a.x")], at=T0)
    assert a.fingerprint == b.fingerprint


def test_exception_grant_without_justification_is_rejected() -> None:
    with pytest.raises(ValueError, match="justification"):
        Grant(
            scope="stripe.refund",
            source=GrantSource.EXPLICIT_GRANT,
            valid_from=T0,
            justification="   ",
        )


# --------------------------------------------------------------------------- #
# THE INVARIANT — exhaustive over the scope universe
# --------------------------------------------------------------------------- #


def _powerset(items):
    for r in range(len(items) + 1):
        yield from itertools.combinations(items, r)


def test_effective_scope_never_widens_exhaustive() -> None:
    """256 * 16 combinations. If any input produces a scope outside the
    intersection, this fails."""
    checked = 0
    for grants in _powerset(UNIVERSE):
        snap = compile_authority(_principal(), [_g(s) for s in grants], at=T0)
        for contract in _powerset(UNIVERSE):
            ceiling = frozenset(contract) & frozenset(grants)
            for requested in _powerset(UNIVERSE):
                req = frozenset(requested)
                checked += 1
                if req <= ceiling:
                    got = resolve_effective_scope(
                        requested=req, contract_tools=contract, snapshot=snap
                    )
                    assert got <= ceiling
                    assert got == req
                else:
                    with pytest.raises(AuthorityExceeded):
                        resolve_effective_scope(
                            requested=req, contract_tools=contract, snapshot=snap
                        )
    assert checked == 16 * 16 * 16


def test_denial_names_the_excess() -> None:
    snap = compile_authority(_principal(), [_g("llm.generate")], at=T0)
    with pytest.raises(AuthorityExceeded) as ei:
        resolve_effective_scope(
            requested=["llm.generate", "stripe.refund"],
            contract_tools=UNIVERSE,
            snapshot=snap,
        )
    assert ei.value.excess == ["stripe.refund"]
    assert ei.value.failed_stage == "scope"


def test_agent_cannot_exceed_human_even_with_permissive_contract() -> None:
    """The whole point of the per-employee shape."""
    snap = compile_authority(_principal(), [_g("memory.search")], at=T0)
    with pytest.raises(AuthorityExceeded):
        resolve_effective_scope(
            requested=["stripe.refund"],
            contract_tools=UNIVERSE,  # contract allows everything
            snapshot=snap,  # human does not
        )


# --------------------------------------------------------------------------- #
# Delegation
# --------------------------------------------------------------------------- #


def test_subagent_scope_narrows() -> None:
    snap = compile_authority(
        _principal(), [_g("llm.generate"), _g("memory.search")], at=T0
    )
    got = attenuate_for_subagent(
        parent_scope=["llm.generate", "memory.search"],
        parent_expires_at=T0 + timedelta(minutes=10),
        child_contract_tools=["llm.generate", "slack.post"],
        child_requested=["llm.generate"],
        child_expires_at=T0 + timedelta(minutes=5),
        snapshot=snap,
    )
    assert got == frozenset({"llm.generate"})


def test_subagent_cannot_reach_outside_parent_scope() -> None:
    snap = compile_authority(
        _principal(), [_g("llm.generate"), _g("slack.post")], at=T0
    )
    with pytest.raises(AuthorityExceeded):
        attenuate_for_subagent(
            parent_scope=["llm.generate"],  # parent never had slack.post
            parent_expires_at=T0 + timedelta(minutes=10),
            child_contract_tools=["slack.post"],
            child_requested=["slack.post"],
            child_expires_at=T0 + timedelta(minutes=5),
            snapshot=snap,
        )


def test_subagent_cannot_outlive_parent() -> None:
    snap = compile_authority(_principal(), [_g("llm.generate")], at=T0)
    with pytest.raises(ExpiryExtensionDenied):
        attenuate_for_subagent(
            parent_scope=["llm.generate"],
            parent_expires_at=T0 + timedelta(minutes=5),
            child_contract_tools=["llm.generate"],
            child_requested=["llm.generate"],
            child_expires_at=T0 + timedelta(minutes=30),
            snapshot=snap,
        )


def test_revocation_detected_without_db_lookup() -> None:
    snap_at_mint = compile_authority(
        _principal(), [_g("llm.generate"), _g("stripe.refund")], at=T0
    )
    claim = OnBehalfOf(
        principal_id="devon",
        authority_fingerprint=snap_at_mint.fingerprint,
        session_kind="cowork",
    )
    assert_snapshot_current(claim, snap_at_mint)  # still valid

    snap_after_revoke = compile_authority(
        _principal(),
        [
            _g("llm.generate"),
            _g("stripe.refund"),
            _g("stripe.refund", GrantSource.EXPLICIT_DENY),
        ],
        at=T0,
    )
    with pytest.raises(StaleAuthority):
        assert_snapshot_current(claim, snap_after_revoke)


# --------------------------------------------------------------------------- #
# Spend ledger — fail-closed
# --------------------------------------------------------------------------- #


class _FakeRepo:
    """In-memory stand-in that reproduces the SQL's conditional-update semantics."""

    def __init__(self, envelope: SpendEnvelope | None) -> None:
        self.envelope = envelope
        self.holds: dict[str, int] = {}

    async def try_reserve(self, *, amount_minor, idempotency_key, now, expires_at, **kw):
        from skylize.app.principal.models import Reservation

        e = self.envelope
        if e is None or e.revoked_at is not None:
            return None
        if not (e.period_start <= now < e.period_end):
            return None
        held = sum(self.holds.values())
        if e.spent_minor + held + amount_minor > e.ceiling_minor:
            return None
        self.holds[idempotency_key] = amount_minor
        return Reservation(
            reservation_id=uuid4(),
            envelope_id=e.envelope_id,
            org_id=e.org_id,
            idempotency_key=idempotency_key,
            amount_minor=amount_minor,
            correlation_id=kw["correlation_id"],
            state="held",
            created_at=now,
            expires_at=expires_at,
        )

    async def get_envelope(self, *, org_id, principal_id, now):
        e = self.envelope
        if e is None:
            return None
        held = sum(self.holds.values())
        return e.model_copy(update={"reserved_minor": held})

    async def commit(self, **kw): ...
    async def release(self, **kw): ...
    async def sweep_expired(self, **kw):
        return 0


def _envelope(**kw) -> SpendEnvelope:
    base = dict(
        envelope_id=uuid4(),
        org_id="org_1",
        principal_id="devon",
        currency="USD",
        ceiling_minor=50_000,
        reserved_minor=0,
        spent_minor=0,
        period_start=T0 - timedelta(days=1),
        period_end=T0 + timedelta(days=29),
        over_ceiling_behavior="hard_deny",
    )
    return SpendEnvelope(**{**base, **kw})


@pytest.mark.asyncio
async def test_missing_envelope_is_denial_not_unlimited() -> None:
    ledger = SpendLedger(_FakeRepo(None))
    with pytest.raises(EnvelopeNotFound):
        await ledger.reserve(
            org_id="org_1",
            principal_id="devon",
            amount_minor=100,
            idempotency_key="run-abc-00001",
            correlation_id=uuid4(),
            now=T0,
        )


@pytest.mark.asyncio
async def test_concurrent_holds_cannot_exceed_ceiling() -> None:
    """The race that a token-embedded ceiling cannot prevent."""
    ledger = SpendLedger(_FakeRepo(_envelope(ceiling_minor=1_000)))
    await ledger.reserve(
        org_id="org_1", principal_id="devon", amount_minor=600,
        idempotency_key="run-aaa-00001", correlation_id=uuid4(), now=T0,
    )
    with pytest.raises(CeilingExceeded) as ei:
        await ledger.reserve(
            org_id="org_1", principal_id="devon", amount_minor=600,
            idempotency_key="run-bbb-00002", correlation_id=uuid4(), now=T0,
        )
    assert ei.value.defer_to_human is False
    assert ei.value.failed_stage == "budget"


@pytest.mark.asyncio
async def test_defer_to_human_is_surfaced_on_the_denial() -> None:
    ledger = SpendLedger(
        _FakeRepo(_envelope(ceiling_minor=100, over_ceiling_behavior="defer_to_human"))
    )
    with pytest.raises(CeilingExceeded) as ei:
        await ledger.reserve(
            org_id="org_1", principal_id="devon", amount_minor=500,
            idempotency_key="run-ccc-00003", correlation_id=uuid4(), now=T0,
        )
    assert ei.value.defer_to_human is True


# --------------------------------------------------------------------------- #
# Brief assembly is deterministic
# --------------------------------------------------------------------------- #


def _entry(seq, kind, actor, attention=False, cost=0) -> JournalEntry:
    return JournalEntry(
        seq=seq,
        org_id="org_1",
        principal_id="devon",
        actor_kind=actor,
        actor_id="cfo_agent",
        correlation_id=uuid4(),
        kind=kind,
        headline=f"{kind} happened",
        cost_minor=cost,
        requires_attention=attention,
        occurred_at=T0 + timedelta(minutes=seq),
    )


def test_brief_separates_agent_work_from_own_work_and_flags_attention() -> None:
    entries = [
        _entry(1, "invoice.reconciled", ActorKind.AGENT_AUTONOMOUS, cost=1200),
        _entry(2, "decision.deferred_to_human", ActorKind.AGENT_AUTONOMOUS, attention=True),
        _entry(3, "brief.reviewed", ActorKind.HUMAN),
    ]
    brief = assemble_brief(entries)
    assert brief["entry_count"] == 3
    assert brief["total_cost_minor"] == 1200
    assert len(brief["needs_attention"]) == 1
    assert len(brief["done_while_away"]) == 2
    assert len(brief["your_own_actions"]) == 1
    assert brief["head_seq"] == 3


def test_empty_brief_is_stable() -> None:
    brief = assemble_brief([])
    assert brief["entry_count"] == 0
    assert brief["head_seq"] == 0
    assert brief["window_start"] is None
