"""The fifth ToolProxy gate: Stripe Connect trust, and the [INTERIM-RULE]
large-refund review trigger it carries for refund-verb tools.

Design: docs/06_integrations/stripe_connector_design.md 4.5.4, 7.5.4.
Mirrors tests/unit/test_gcp_wif_gate_and_trigger.py's harness pattern: the
real `_authorize_stripe` is exercised through a `ToolProxy.__new__` instance
with only the attributes the gate reads set directly, no full ToolProxy
construction needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from skylize.dal.refund_limits import OrgRefundLimitsDAL
from skylize.dal.stripe_accounts import InMemoryStripeAccountRepository, StripeAccountRow
from skylize.tools.base import (
    ToolSpendDeferredToHuman,
    ToolSpendProfile,
    ToolStripeChargeTypeForbidden,
    ToolStripeNotConnected,
    ToolStripeProfile,
    ToolStripeScopeInsufficient,
)

ORG = "org_test"


def _account_row(
    org: str = ORG, *, livemode: bool = False, scope: str = "read_write",
    deauthorized: bool = False,
) -> StripeAccountRow:
    now = datetime.now(timezone.utc)
    return StripeAccountRow(
        id=uuid.uuid4(), org_id=org, stripe_account_id="acct_fake",
        livemode=livemode, scope=scope, connected_at=now,
        deauthorized_at=now if deauthorized else None,
    )


class _RefundInput:
    """Stands in for a validated tool input; the gate reads named attributes."""

    def __init__(
        self, *, amount_minor: int = 500,
        on_behalf_of=None, transfer_data=None, application_fee_amount=None,
    ) -> None:
        self.amount_minor = amount_minor
        self.on_behalf_of = on_behalf_of
        self.transfer_data = transfer_data
        self.application_fee_amount = application_fee_amount


class _NonRefundInput:
    """A stripe-gated tool with NO spend profile - the [INTERIM-RULE] check
    must not run for it at all."""


class _Contract:
    agent_id = "test_agent"
    department = "finance"


class _Token:
    def __init__(self, authority_level: str = "worker") -> None:
        self.authority_level = authority_level


def _tool(*, spend: bool = True, mutates_money: bool = True):
    spend_profile = (
        ToolSpendProfile(currency="usd", amount_field="amount_minor") if spend else None
    )

    class _Tool:
        tool_id = "stripe.refund"
        stripe = ToolStripeProfile(required_scope="read_write", mutates_money=mutates_money)

    _Tool.spend = spend_profile  # type: ignore[attr-defined]
    return _Tool()


async def _authorize(
    *, stripe_repo=None, refund_limits=None, stripe_livemode: bool = False,
    tool=None, inp=None, token=None,
):
    """Call the real `_authorize_stripe` with the minimum surrounding scaffolding."""
    from skylize.tools.proxy import ToolProxy

    proxy = ToolProxy.__new__(ToolProxy)
    proxy._stripe_repo = stripe_repo  # type: ignore[attr-defined]
    proxy._stripe_livemode = stripe_livemode  # type: ignore[attr-defined]
    proxy._refund_limits = refund_limits  # type: ignore[attr-defined]

    async def _noop(**_kw):
        return None

    proxy._audit_call = _noop  # type: ignore[attr-defined]
    await ToolProxy._authorize_stripe(
        proxy, tool=tool or _tool(), validated_input=inp or _RefundInput(),
        contract=_Contract(), org_id=ORG, correlation_id=uuid.uuid4(),  # type: ignore[arg-type]
        governance_token=token or _Token(),  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# design 4.5.4 checks 0-3
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_gate_fails_closed_when_no_stripe_repo_is_wired() -> None:
    """A tool that moves customer money must never dispatch because the CHECK
    was missing (design 4.5.4 check 0)."""
    with pytest.raises(ToolStripeNotConnected, match="no Stripe account store"):
        await _authorize(stripe_repo=None)


@pytest.mark.asyncio
async def test_gate_refuses_when_the_org_has_no_connected_account() -> None:
    repo = InMemoryStripeAccountRepository()
    with pytest.raises(ToolStripeNotConnected, match="no Stripe account connected"):
        await _authorize(stripe_repo=repo)


@pytest.mark.asyncio
async def test_gate_refuses_a_live_call_against_a_test_only_connection() -> None:
    """NO fallback between modes in either direction (design 4.0.2): a live
    process must never silently act through a test connection."""
    repo = InMemoryStripeAccountRepository()
    await repo.insert(_account_row(livemode=False))  # test-mode only
    with pytest.raises(ToolStripeNotConnected, match="live"):
        await _authorize(stripe_repo=repo, stripe_livemode=True)


@pytest.mark.asyncio
async def test_gate_refuses_a_test_call_against_a_live_only_connection() -> None:
    repo = InMemoryStripeAccountRepository()
    await repo.insert(_account_row(livemode=True))  # live-mode only
    with pytest.raises(ToolStripeNotConnected, match="test"):
        await _authorize(stripe_repo=repo, stripe_livemode=False)


@pytest.mark.asyncio
async def test_gate_refuses_a_deauthorized_connection() -> None:
    repo = InMemoryStripeAccountRepository()
    await repo.insert(_account_row(livemode=False, deauthorized=True))
    with pytest.raises(ToolStripeNotConnected):
        await _authorize(stripe_repo=repo, stripe_livemode=False)


@pytest.mark.asyncio
async def test_gate_refuses_insufficient_granted_scope() -> None:
    """design 4.5.4 check 2: checked against what Stripe GRANTED, never what
    was requested."""
    repo = InMemoryStripeAccountRepository()
    await repo.insert(_account_row(scope="read_only"))
    with pytest.raises(ToolStripeScopeInsufficient, match="read_only"):
        await _authorize(stripe_repo=repo)


@pytest.mark.asyncio
async def test_gate_allows_matching_or_wider_granted_scope() -> None:
    repo = InMemoryStripeAccountRepository()
    await repo.insert(_account_row(scope="read_write"))
    await _authorize(stripe_repo=repo)  # must not raise


@pytest.mark.parametrize(
    "kwargs",
    [
        {"on_behalf_of": "acct_other"},
        {"transfer_data": {"destination": "acct_other"}},
        {"application_fee_amount": 100},
    ],
)
@pytest.mark.asyncio
async def test_gate_refuses_any_destination_charge_field(kwargs: dict) -> None:
    """design 2.0's hard constraint: direct charges only."""
    repo = InMemoryStripeAccountRepository()
    await repo.insert(_account_row())
    with pytest.raises(ToolStripeChargeTypeForbidden):
        await _authorize(stripe_repo=repo, inp=_RefundInput(**kwargs))


@pytest.mark.asyncio
async def test_gate_allows_a_clean_direct_charge_refund() -> None:
    repo = InMemoryStripeAccountRepository()
    await repo.insert(_account_row())
    await _authorize(stripe_repo=repo)  # must not raise


@pytest.mark.asyncio
async def test_gate_skips_the_charge_type_check_when_mutates_money_is_false() -> None:
    repo = InMemoryStripeAccountRepository()
    await repo.insert(_account_row())
    await _authorize(
        stripe_repo=repo,
        tool=_tool(mutates_money=False),
        inp=_RefundInput(on_behalf_of="acct_other"),
    )  # must not raise - the check is off for this (hypothetical) tool


# --------------------------------------------------------------------------- #
# design 7.5.4 [INTERIM-RULE] large-refund review trigger
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_review_defers_when_no_authority_limit_is_configured() -> None:
    """Fail-closed-empty (design 7.5.2): a missing row DENIES authority-level
    authorization outright."""
    repo = InMemoryStripeAccountRepository()
    await repo.insert(_account_row())

    class _NullConn:
        async def fetchval(self, *a, **kw):
            return None


    class _FakeDb:
        def tenant_session(self, org_id):
            class _Ctx:
                async def __aenter__(self):
                    return _NullConn()

                async def __aexit__(self, *exc):
                    return False

            return _Ctx()

    limits = OrgRefundLimitsDAL(db=_FakeDb())  # type: ignore[arg-type]

    with pytest.raises(ToolSpendDeferredToHuman, match="authority cap"):
        await _authorize(stripe_repo=repo, refund_limits=limits)


@pytest.mark.asyncio
async def test_review_defers_when_amount_exceeds_the_authority_cap() -> None:
    repo = InMemoryStripeAccountRepository()
    await repo.insert(_account_row())

    class _Conn:
        async def fetchval(self, sql, *args):
            if "org_refund_authority_limits" in sql:
                return 100  # cap is 100, refund is 500
            return None

    class _FakeDb:
        def tenant_session(self, org_id):
            class _Ctx:
                async def __aenter__(self):
                    return _Conn()

                async def __aexit__(self, *exc):
                    return False

            return _Ctx()

    limits = OrgRefundLimitsDAL(db=_FakeDb())  # type: ignore[arg-type]

    with pytest.raises(ToolSpendDeferredToHuman, match="exceeds"):
        await _authorize(stripe_repo=repo, refund_limits=limits, inp=_RefundInput(amount_minor=500))


@pytest.mark.asyncio
async def test_review_defers_when_amount_is_at_or_above_review_threshold() -> None:
    class _Conn:
        async def fetchval(self, sql, *args):
            if "org_refund_authority_limits" in sql:
                return 10_000  # authority cap plenty high
            if "org_refund_review_thresholds" in sql:
                return 500  # review threshold is 500, refund is 500 - at, not above
            return None

    class _FakeDb:
        def tenant_session(self, org_id):
            class _Ctx:
                async def __aenter__(self):
                    return _Conn()

                async def __aexit__(self, *exc):
                    return False

            return _Ctx()

    limits = OrgRefundLimitsDAL(db=_FakeDb())  # type: ignore[arg-type]

    repo = InMemoryStripeAccountRepository()
    await repo.insert(_account_row())

    with pytest.raises(ToolSpendDeferredToHuman, match="review threshold"):
        await _authorize(stripe_repo=repo, refund_limits=limits, inp=_RefundInput(amount_minor=500))


@pytest.mark.asyncio
async def test_review_allows_a_refund_within_both_caps() -> None:
    class _Conn:
        async def fetchval(self, sql, *args):
            if "org_refund_authority_limits" in sql:
                return 10_000
            if "org_refund_review_thresholds" in sql:
                return 10_000
            return None

    class _FakeDb:
        def tenant_session(self, org_id):
            class _Ctx:
                async def __aenter__(self):
                    return _Conn()

                async def __aexit__(self, *exc):
                    return False

            return _Ctx()

    limits = OrgRefundLimitsDAL(db=_FakeDb())  # type: ignore[arg-type]

    repo = InMemoryStripeAccountRepository()
    await repo.insert(_account_row())

    await _authorize(
        stripe_repo=repo, refund_limits=limits, inp=_RefundInput(amount_minor=500),
    )  # must not raise


@pytest.mark.asyncio
async def test_review_rule_does_not_run_for_a_non_spend_stripe_tool() -> None:
    """The [INTERIM-RULE] compares against ToolSpendProfile.amount_field; a
    stripe-gated tool with no spend profile has nothing to compare and must
    not be reached by this check at all."""
    repo = InMemoryStripeAccountRepository()
    await repo.insert(_account_row())

    class _ExplodingLimits:
        async def read_authority_limit_minor(self, *a, **kw):
            raise AssertionError("must not be called for a non-spend tool")

    await _authorize(
        stripe_repo=repo,
        refund_limits=_ExplodingLimits(),  # type: ignore[arg-type]
        tool=_tool(spend=False),
        inp=_NonRefundInput(),
    )  # must not raise, and must not touch refund_limits at all


@pytest.mark.asyncio
async def test_review_rule_does_not_run_when_refund_limits_is_not_wired() -> None:
    """None where no refund-limits infrastructure is wired - the [INTERIM-RULE]
    is additional, narrower machinery on top of Q2.1c's HITL-replay-always
    resolution, not the only gate standing between an agent and a refund."""
    repo = InMemoryStripeAccountRepository()
    await repo.insert(_account_row())

    await _authorize(stripe_repo=repo, refund_limits=None)  # must not raise
