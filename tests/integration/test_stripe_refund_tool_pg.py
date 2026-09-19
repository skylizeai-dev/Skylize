"""stripe.refund through the REAL ToolProxy, against REAL Postgres.

NOT RUN IN THIS SESSION - see test_stripe_refund_foundation_pg.py's module
docstring for the same caveat: no SKYLIZE_TEST_DB_URL / SKYLIZE_TEST_APP_DB_URL
were configured, so these tests have never been executed. Written by direct
reading of tools/proxy.py's replay-guard/settlement machinery (already proven
generically by test_tool_proxy_replay_states_pg.py and
test_tool_proxy_settlement.py) and app/stripe/actions.py, tools/builtin/
stripe_tools.py.

This file proves ONE claim the unit suite (test_stripe_gate.py,
test_stripe_actions.py) cannot: that a replayed HITL approval of a refund
does not double-refund at Stripe or double-commit at the ledger, end to end
through the real ToolProxy.invoke, against the real spend_reservation table
and its migration-0030 partial unique index - not a fake repository
asserting the invariant it was written to satisfy.

The Stripe HTTP call itself is still faked (a `_Recorder`, same shape as
tests/unit/test_stripe_actions.py) - this suite tests the LEDGER's replay
safety, not a live Stripe integration. No real Stripe call is made anywhere
in this repository; see app/stripe/actions.py's module docstring for why
that boundary is deliberate.

Everything runs as the non-superuser, non-table-owner `skylize_app` role, so
RLS is genuinely in force. Skipped unless SKYLIZE_TEST_DB_URL and
SKYLIZE_TEST_APP_DB_URL are both set.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from skylize.app.audit.service import AuditService
from skylize.app.governance import GovernanceAuthority
from skylize.app.principal.provider import (
    InMemoryPrincipalRepository,
    PrincipalAuthorityService,
)
from skylize.app.principal.models import Grant, GrantSource, Principal
from skylize.app.principal.spend import SpendLedger
from skylize.app.stripe.actions import StripeRefundExecutor
from skylize.config import Settings
from skylize.contracts.base import AgentContract, FailureMode, ToolGrant
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.memory import InMemoryAuditRepository, InMemoryGovernanceRepository
from skylize.dal.spend_reservation import PostgresSpendRepository
from skylize.dal.stripe_accounts import InMemoryStripeAccountRepository, StripeAccountRow
from skylize.events.memory_bus import InMemoryEventBus
from skylize.tools.builtin.stripe_tools import (
    STRIPE_REFUND_CURRENCY,
    build_stripe_refund_tool,
)
from skylize.tools.proxy import ToolProxy
from skylize.tools.registry import ToolRegistry

from .conftest import APP_DB_URL, requires_app_role
from .test_tool_proxy_spend_pg import PRINCIPAL, _drop_org, _seed_envelope

pytestmark = pytest.mark.integration

HITL_ID = uuid.uuid4()
TOOL_USE_ID = "toolu_stripe_refund_fixture"
CHARGE_ID = "ch_fixture"


class _Recorder:
    """Fakes the Stripe HTTP boundary only - everything else here is real."""

    def __init__(self, *, amount_refunded: int) -> None:
        self.post_calls: list[dict] = []
        self._amount_refunded = amount_refunded

    def __call__(self, **_kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url: str, **kw):
        return httpx.Response(200, json={"id": CHARGE_ID, "currency": STRIPE_REFUND_CURRENCY})

    async def post(self, url: str, **kw):
        self.post_calls.append(dict(kw.get("headers") or {}))
        return httpx.Response(
            200,
            json={
                "id": "re_fixture", "status": "succeeded",
                "amount": self._amount_refunded, "currency": STRIPE_REFUND_CURRENCY,
            },
        )


async def _refund_proxy(*, org: str, ledger: SpendLedger, recorder: _Recorder):
    """A real ToolProxy wired with the real stripe.refund tool.

    Returns (proxy, contract, token, correlation_id).
    """
    stripe_repo = InMemoryStripeAccountRepository()
    await stripe_repo.insert(StripeAccountRow(
        id=uuid.uuid4(), org_id=org, stripe_account_id="acct_fixture",
        livemode=False, scope="read_write",
        connected_at=datetime.now(timezone.utc), deauthorized_at=None,
    ))

    def executor_factory() -> StripeRefundExecutor:
        return StripeRefundExecutor(
            platform_secret_key="sk_test_fixture", http_client_factory=recorder,
        )

    tools = build_stripe_refund_tool(
        stripe_repo=stripe_repo, executor_factory=executor_factory, stripe_livemode=False,
    )
    registry = ToolRegistry(tools)

    bus = InMemoryEventBus()
    audit = AuditService(bus, InMemoryAuditRepository())
    prepo = InMemoryPrincipalRepository()
    prepo.add_principal(
        Principal(
            principal_id=PRINCIPAL, org_id=org, display_name="Devon",
            authority_level="manager",
        )
    )
    prepo.add_grant(
        org_id=org, principal_id=PRINCIPAL,
        grant=Grant(
            scope="stripe.refund", source=GrantSource.POSITION,
            valid_from=datetime.now(timezone.utc) - timedelta(days=1),
        ),
    )
    authority = GovernanceAuthority.build(
        repo=InMemoryGovernanceRepository(), audit=audit, bus=bus,
        registry=MVP_REGISTRY, settings=Settings(backend="memory"),
        principal_authority=PrincipalAuthorityService(prepo),
    )
    contract = AgentContract(
        agent_id="refund_test_agent", agent_role="E2E", authority_level="manager",
        department="finance",
        input_schema="skylize.runtime.agent_runner.AgentRunInput",
        output_schema="skylize.runtime.agent_runner.AgentRunResult",
        allowed_tools=[ToolGrant(tool_id="stripe.refund", purpose="test")],
        max_token_budget=8_000, max_execution_time_seconds=60,
        escalation_path=["human_owner"],
        failure_mode=FailureMode.FALLBACK_DEGRADED,
        memory_read_access=[], memory_write_access=[],
    )
    # No refund_limits wired: this suite proves REPLAY/SETTLEMENT safety, not
    # the [INTERIM-RULE] review trigger (already covered by test_stripe_gate.py
    # at the unit level). Design 7.5.4's own reasoning applies - the
    # [INTERIM-RULE] is additional, narrower machinery on top of Q2.1c, not
    # the only thing standing between an agent and a refund.
    proxy = ToolProxy(
        registry=registry, audit=audit, public_key=authority.public_key,
        live_state_for=authority.live_state_checker, spend_ledger=ledger,
        stripe_repo=stripe_repo, stripe_livemode=False,
    )

    corr = uuid.uuid4()
    token = await authority.mint(
        contract, org_id=org, correlation_id=corr, on_behalf_of_principal=PRINCIPAL,
    )
    assert token.on_behalf_of is not None
    return proxy, contract, token, corr


@requires_app_role
async def test_a_replayed_hitl_approval_does_not_double_refund_or_double_commit() -> None:
    """THE PROPERTY THIS FILE EXISTS TO PROVE.

    Two `invoke` calls carrying the SAME (hitl_id, tool_use_id) - simulating a
    human retrying an approval after a transient failure - must dispatch the
    Stripe call at most ONCE and commit the ledger at most ONCE. The second
    call must return the RECORDED result (migration 0030's replay_key +
    result_snapshot), never re-execute.
    """
    import asyncpg

    org = await _seed_envelope(ceiling_minor=100_000)
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=1, max_size=3)
    try:
        ledger = SpendLedger(PostgresSpendRepository(pool))
        recorder = _Recorder(amount_refunded=500)
        proxy, contract, token, corr = await _refund_proxy(
            org=org, ledger=ledger, recorder=recorder,
        )

        input_data = {
            "charge_id": CHARGE_ID, "amount_minor": 500, "reason": "customer requested",
        }

        first = await proxy.invoke(
            tool_id="stripe.refund", input_data=input_data,
            governance_token=token, contract=contract, org_id=org,
            correlation_id=corr, hitl_id=HITL_ID, tool_use_id=TOOL_USE_ID,
            is_hitl_resumption=True,
        )
        second = await proxy.invoke(
            tool_id="stripe.refund", input_data=input_data,
            governance_token=token, contract=contract, org_id=org,
            correlation_id=uuid.uuid4(),  # a FRESH correlation_id, exactly as a
            # real HITL retry mints (design 7.0.1) - hitl_id/tool_use_id are
            # what must carry the replay identity, not this.
            hitl_id=HITL_ID, tool_use_id=TOOL_USE_ID, is_hitl_resumption=True,
        )

        assert len(recorder.post_calls) == 1, (
            "the second invoke must NOT call Stripe again - it must return the "
            "recorded result"
        )
        assert first.output.model_dump() == second.output.model_dump()
    finally:
        await pool.close()
        await _drop_org(org)


@requires_app_role
async def test_settlement_commits_the_actual_refunded_amount_not_the_reservation() -> None:
    """Stripe refunded LESS than requested (a charge partially refunded
    already). The ledger hold must settle to the ACTUAL amount, per the
    2026-09-16 refund-tool-scoped authorization to consume PR #14's generic
    settlement mechanism - proven here against the real spend_reservation
    table, not a fake asserting its own contract.
    """
    import asyncpg

    org = await _seed_envelope(ceiling_minor=100_000)
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=1, max_size=3)
    try:
        ledger = SpendLedger(PostgresSpendRepository(pool))
        # Requested 500, Stripe actually refunded only 300.
        recorder = _Recorder(amount_refunded=300)
        proxy, contract, token, corr = await _refund_proxy(
            org=org, ledger=ledger, recorder=recorder,
        )

        result = await proxy.invoke(
            tool_id="stripe.refund",
            input_data={"charge_id": CHARGE_ID, "amount_minor": 500, "reason": "test"},
            governance_token=token, contract=contract, org_id=org,
            correlation_id=corr, hitl_id=uuid.uuid4(), tool_use_id="toolu_settlement",
            is_hitl_resumption=True,
        )
        assert result.output.actual_refunded_minor == 300  # type: ignore[attr-defined]

        envelope = await ledger.get_envelope(
            org_id=org, principal_id=PRINCIPAL, now=datetime.now(timezone.utc),
        )
        assert envelope is not None
        # Only 300 was ever really spent - the 200 difference must be back in
        # the spendable pool, not held against a refund that only partially
        # went through.
        assert envelope.spent_minor == 300
    finally:
        await pool.close()
        await _drop_org(org)


@requires_app_role
async def test_a_refund_with_no_stripe_account_connected_fails_closed() -> None:
    """The gate, not the handler, must be what refuses - proven end to end
    through invoke rather than by calling _authorize_stripe directly (already
    covered at the unit level by test_stripe_gate.py)."""
    import asyncpg

    org = await _seed_envelope(ceiling_minor=100_000)
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=1, max_size=3)
    try:
        from skylize.tools.base import ToolStripeNotConnected

        ledger = SpendLedger(PostgresSpendRepository(pool))
        recorder = _Recorder(amount_refunded=500)
        proxy, contract, token, corr = await _refund_proxy(
            org=org, ledger=ledger, recorder=recorder,
        )
        # Deauthorize the only connected account before invoking.
        proxy._stripe_repo = InMemoryStripeAccountRepository()  # type: ignore[attr-defined]

        with pytest.raises(ToolStripeNotConnected):
            await proxy.invoke(
                tool_id="stripe.refund",
                input_data={"charge_id": CHARGE_ID, "amount_minor": 500, "reason": "test"},
                governance_token=token, contract=contract, org_id=org,
                correlation_id=corr, hitl_id=uuid.uuid4(), tool_use_id="toolu_no_account",
                is_hitl_resumption=True,
            )
        assert recorder.post_calls == [], "no money may move when the gate refuses"
    finally:
        await pool.close()
        await _drop_org(org)
