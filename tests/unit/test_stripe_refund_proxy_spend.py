"""The REAL `stripe.refund` tool through the REAL `ToolProxy.invoke` spend path.

Until now the spend gate was exercised in unit tests only with a synthetic tool
(tests/unit/test_tool_proxy_spend.py) and the real refund handler only without
the proxy (tests/unit/test_stripe_tools_handler.py). This file joins them:
real tool definition, real `StripeRefundExecutor`, real proxy gates, a real
`GovernanceAuthority` minting tokens, a fake spend repository (contract of
`PostgresSpendRepository`, not its SQL) and a recording fake HTTP client.

No network: every Stripe call is answered by `_StripeFake`. Fake keys only.
This exercises PRE-EXISTING code; it does not by itself guard the bootstrap
wiring (tests/unit/test_bootstrap_stripe_wiring.py does that).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from skylize.app.audit.service import AuditService
from skylize.app.governance import GovernanceAuthority
from skylize.app.principal.models import (
    Grant,
    GrantSource,
    Principal,
    Reservation,
    SpendEnvelope,
)
from skylize.app.principal.provider import (
    InMemoryPrincipalRepository,
    PrincipalAuthorityService,
)
from skylize.app.principal.spend import SpendLedger
from skylize.app.stripe.actions import StripeRefundExecutor, idempotency_key_for
from skylize.config import Settings
from skylize.contracts.base import AgentContract, FailureMode, ToolGrant
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.memory import InMemoryAuditRepository, InMemoryGovernanceRepository
from skylize.dal.stripe_accounts import InMemoryStripeAccountRepository, StripeAccountRow
from skylize.events.memory_bus import InMemoryEventBus
from skylize.tools.base import ToolExecutionError, ToolStripeNotConnected
from skylize.tools.builtin.stripe_tools import (
    STRIPE_CREATE_REFUND_TOOL_ID,
    build_stripe_refund_tool,
)
from skylize.tools.proxy import ToolProxy
from skylize.tools.registry import ToolRegistry

ORG = "org_stripe_spend"
PRINCIPAL = "devon"
AGENT = "stripe_refund_spend_test_agent"
ACCT = "acct_fixture"
CHARGE_ID = "ch_fixture"
TEST_KEY = "sk_test_dummy"


# --------------------------------------------------------------------------- #
# Fakes (one shared event log so ORDER across ledger and HTTP is assertable)
# --------------------------------------------------------------------------- #


class FakeSpendRepo:
    """`PostgresSpendRepository`'s contract, not its SQL (copied in miniature
    from tests/unit/test_tool_proxy_spend.py), recording into `events`."""

    def __init__(self, events: list[str], *, ceiling_minor: int = 100_000) -> None:
        self.events = events
        self.envelope_id = uuid4()
        self.ceiling_minor = ceiling_minor
        self.reserved_minor = 0
        self.spent_minor = 0
        self._reservations: dict[UUID, Reservation] = {}
        self.reserves: list[int] = []
        self.commits: list[int] = []
        self.releases: list[UUID] = []

    async def try_reserve(
        self, *, org_id: str, principal_id: str, amount_minor: int,
        idempotency_key: Any, correlation_id: UUID, governance_token_id: Any,
        now: datetime, expires_at: datetime, replay_key: Any = None,
    ) -> Reservation | None:
        if self.spent_minor + self.reserved_minor + amount_minor > self.ceiling_minor:
            return None
        self.events.append(f"reserve:{amount_minor}")
        self.reserves.append(amount_minor)
        self.reserved_minor += amount_minor
        res = Reservation(
            reservation_id=uuid4(), envelope_id=self.envelope_id, org_id=org_id,
            idempotency_key=idempotency_key, amount_minor=amount_minor,
            correlation_id=correlation_id, governance_token_id=governance_token_id,
            state="held", created_at=now, expires_at=expires_at, replay_key=replay_key,
        )
        self._reservations[res.reservation_id] = res
        return res

    async def commit(
        self, *, org_id: str, reservation_id: UUID, actual_minor: int,
        now: datetime, result_snapshot: Any = None,
    ) -> None:
        res = self._reservations.get(reservation_id)
        if res is None or res.state != "held":
            return
        actual = min(actual_minor, res.amount_minor)
        self.events.append(f"commit:{actual}")
        self.reserved_minor -= res.amount_minor
        self.spent_minor += actual
        self._reservations[reservation_id] = res.model_copy(
            update={"state": "committed", "committed_minor": actual}
        )
        self.commits.append(actual)

    async def find_by_replay_key(self, *, org_id: str, replay_key: Any) -> Reservation | None:
        for res in self._reservations.values():
            if res.replay_key == replay_key and res.state in ("held", "committed"):
                return res
        return None

    async def release(self, *, org_id: str, reservation_id: UUID, now: datetime) -> None:
        res = self._reservations.get(reservation_id)
        if res is None or res.state != "held":
            return
        self.events.append("release")
        self.reserved_minor -= res.amount_minor
        self._reservations[reservation_id] = res.model_copy(update={"state": "released"})
        self.releases.append(reservation_id)

    async def get_envelope(
        self, *, org_id: str, principal_id: str, now: datetime
    ) -> SpendEnvelope | None:
        return SpendEnvelope(
            envelope_id=self.envelope_id, org_id=org_id, principal_id=principal_id,
            currency="USD", ceiling_minor=self.ceiling_minor,
            reserved_minor=self.reserved_minor, spent_minor=self.spent_minor,
            period_start=now - timedelta(days=1), period_end=now + timedelta(days=30),
            over_ceiling_behavior="hard_deny",
        )

    async def sweep_expired(self, *, now: datetime, limit: int = 500) -> int:
        return 0

    def held(self) -> list[Reservation]:
        return [r for r in self._reservations.values() if r.state == "held"]


class _StripeFake:
    """Recording stand-in for `httpx.AsyncClient` (shape of `_Recorder` in
    tests/unit/test_stripe_tools_handler.py)."""

    def __init__(
        self, events: list[str], *, refunded_minor: int, post_raises: Exception | None = None
    ) -> None:
        self.events = events
        self.refunded_minor = refunded_minor
        self.post_raises = post_raises
        self.gets: list[dict[str, Any]] = []
        self.posts: list[dict[str, Any]] = []

    def __call__(self, **_kw: Any) -> "_StripeFake":
        return self

    async def __aenter__(self) -> "_StripeFake":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get(self, url: str, **kw: Any) -> httpx.Response:
        self.events.append("http:get_charge")
        self.gets.append({"url": url, **kw})
        return httpx.Response(200, json={"id": CHARGE_ID, "currency": "usd"})

    async def post(self, url: str, **kw: Any) -> httpx.Response:
        self.events.append("http:post_refund")
        self.posts.append({"url": url, **kw})
        if self.post_raises is not None:
            raise self.post_raises
        return httpx.Response(
            200, json={"id": "re_fixture", "status": "succeeded",
                       "amount": self.refunded_minor, "currency": "usd"},
        )


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def _contract() -> AgentContract:
    return AgentContract(
        agent_id=AGENT,
        agent_role="Stripe refund spend test",
        authority_level="worker",
        department="finance",
        input_schema="skylize.runtime.agent_runner.AgentRunInput",
        output_schema="skylize.runtime.agent_runner.AgentRunResult",
        allowed_tools=[ToolGrant(tool_id=STRIPE_CREATE_REFUND_TOOL_ID, purpose="test")],
        max_token_budget=8_000,
        max_execution_time_seconds=60,
        escalation_path=["human_owner"],
        failure_mode=FailureMode.FALLBACK_DEGRADED,
        memory_read_access=[],
        memory_write_access=[],
    )


def _authority() -> tuple[GovernanceAuthority, AuditService]:
    bus = InMemoryEventBus()
    audit = AuditService(bus, InMemoryAuditRepository())
    repo = InMemoryPrincipalRepository()
    repo.add_principal(Principal(
        principal_id=PRINCIPAL, org_id=ORG, display_name="Devon",
        authority_level="manager",
    ))
    repo.add_grant(org_id=ORG, principal_id=PRINCIPAL, grant=Grant(
        scope=STRIPE_CREATE_REFUND_TOOL_ID, source=GrantSource.POSITION,
        valid_from=datetime.now(timezone.utc) - timedelta(days=1),
    ))
    authority = GovernanceAuthority.build(
        repo=InMemoryGovernanceRepository(), audit=audit, bus=bus,
        registry=MVP_REGISTRY, settings=Settings(_env_file=None, backend="memory"),  # type: ignore[call-arg]
        principal_authority=PrincipalAuthorityService(repo),
    )
    return authority, audit


async def _accounts(*, livemode: bool | None) -> InMemoryStripeAccountRepository:
    repo = InMemoryStripeAccountRepository()
    if livemode is not None:
        await repo.insert(StripeAccountRow(
            id=uuid.uuid4(), org_id=ORG, stripe_account_id=ACCT, livemode=livemode,
            scope="read_write", connected_at=datetime.now(timezone.utc),
            deauthorized_at=None,
        ))
    return repo


class _World:
    def __init__(self, *, livemode_row: bool | None = False, refunded_minor: int = 400,
                 post_raises: Exception | None = None) -> None:
        self.events: list[str] = []
        self.ledger = FakeSpendRepo(self.events)
        self.stripe = _StripeFake(
            self.events, refunded_minor=refunded_minor, post_raises=post_raises,
        )
        self._livemode_row = livemode_row

    async def build(self) -> "_World":
        accounts = await _accounts(livemode=self._livemode_row)
        stripe = self.stripe

        def executor_factory() -> StripeRefundExecutor:
            return StripeRefundExecutor(
                platform_secret_key=TEST_KEY, http_client_factory=stripe,
            )

        # stripe_livemode=False at both sites, as bootstrap.py wires it.
        tools = build_stripe_refund_tool(
            stripe_repo=accounts, executor_factory=executor_factory, stripe_livemode=False,
        )
        self.authority, audit = _authority()
        self.proxy = ToolProxy(
            registry=ToolRegistry(tools), audit=audit,
            public_key=self.authority.public_key,
            live_state_for=self.authority.live_state_checker,
            spend_ledger=SpendLedger(self.ledger),
            stripe_repo=accounts, stripe_livemode=False,
        )
        return self

    async def refund(self, *, amount_minor: int = 500, hitl_id: UUID | None) -> Any:
        contract = _contract()
        corr = uuid4()
        token = await self.authority.mint(
            contract, org_id=ORG, correlation_id=corr, on_behalf_of_principal=PRINCIPAL,
        )
        return await self.proxy.invoke(
            tool_id=STRIPE_CREATE_REFUND_TOOL_ID,
            input_data={"charge_id": CHARGE_ID, "amount_minor": amount_minor,
                        "reason": "customer request (test)"},
            governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
            hitl_id=hitl_id,
        )


# --------------------------------------------------------------------------- #
# (a) happy path: reserve -> GET charge -> POST refund -> settle for ACTUAL
# --------------------------------------------------------------------------- #


async def test_refund_reserves_posts_with_headers_then_settles_actual() -> None:
    world = await _World(refunded_minor=400).build()
    hitl_id = uuid4()

    result = await world.refund(amount_minor=500, hitl_id=hitl_id)

    assert world.events == [
        "reserve:500", "http:get_charge", "http:post_refund", "commit:400",
    ]
    assert len(world.stripe.posts) == 1
    post = world.stripe.posts[0]
    assert post["url"].endswith("/refunds")
    headers = post["headers"]
    assert headers["Stripe-Account"] == ACCT
    assert headers["Idempotency-Key"] == str(idempotency_key_for(hitl_id, CHARGE_ID, 500))
    assert headers["Authorization"] == f"Bearer {TEST_KEY}"
    assert post["data"] == {"charge": CHARGE_ID, "amount": 500}
    # Settled for what Stripe actually refunded, not the 500 reserved.
    assert world.ledger.commits == [400]
    assert world.ledger.spent_minor == 400
    assert world.ledger.held() == []
    assert result.output_json()["actual_refunded_minor"] == 400


# --------------------------------------------------------------------------- #
# Refusals: no POST, nothing committed
# --------------------------------------------------------------------------- #


async def test_missing_hitl_id_refuses_before_any_http_and_releases_hold() -> None:
    world = await _World().build()

    with pytest.raises(ToolExecutionError):
        await world.refund(hitl_id=None)

    assert world.stripe.posts == [] and world.stripe.gets == []
    assert world.events == ["reserve:500", "release"]
    assert world.ledger.commits == [] and world.ledger.held() == []


async def test_org_without_connected_account_is_refused_before_reserve() -> None:
    world = await _World(livemode_row=None).build()

    with pytest.raises(ToolStripeNotConnected):
        await world.refund(hitl_id=uuid4())

    assert world.events == []  # no reservation, no HTTP at all
    assert world.ledger.reserves == [] and world.ledger.commits == []


async def test_live_only_account_is_invisible_to_a_test_mode_process() -> None:
    world = await _World(livemode_row=True).build()

    with pytest.raises(ToolStripeNotConnected):
        await world.refund(hitl_id=uuid4())

    assert world.events == []
    assert world.ledger.reserves == [] and world.ledger.commits == []


# --------------------------------------------------------------------------- #
# (e) OPEN RISK, characterised - not fixed here
# --------------------------------------------------------------------------- #


@pytest.mark.xfail(
    strict=True,
    reason=(
        "OPEN RISK for owner decision, not fixed here (outside this department's "
        "allow-list): app/stripe/actions.py:238-248 has no try/except around "
        "client.post; tools/builtin/stripe_tools.py:192 catches only "
        "StripeActionError; tools/proxy.py:494-499 releases the hold on any "
        "handler exception - so a transport timeout AFTER the POST was sent "
        "frees the spend hold although Stripe may have refunded."
    ),
)
async def test_ambiguous_post_timeout_keeps_the_spend_hold() -> None:
    world = await _World(post_raises=httpx.ReadTimeout("timed out")).build()

    with pytest.raises(ToolExecutionError):
        await world.refund(hitl_id=uuid4())

    assert world.stripe.posts, "the POST was attempted"
    # The SAFE behaviour: an ambiguous outcome must not free the hold.
    assert world.ledger.releases == []
