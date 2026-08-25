"""The spend ceiling on the tool-call path (`SpendLedger` wired into `ToolProxy`).

The property under test: a spend-capable tool call reserves against the
principal's envelope BEFORE it dispatches, settles the hold on success, and
releases it on every failure path. A reservation must never outlive the call it
was placed for.

Uses a real `GovernanceAuthority` (in-memory backend) so tokens here are minted
and validated through the exact production pipeline, and a fake `SpendRepository`
that reproduces the adapter's ceiling semantics without Postgres. The ATOMICITY
of the ceiling under concurrency is not — and cannot be — proven here; that lives
in tests/integration/test_tool_proxy_spend_pg.py against real Postgres.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

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
from skylize.config import Settings
from skylize.contracts.base import AgentContract, FailureMode, ToolGrant
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.memory import InMemoryAuditRepository, InMemoryGovernanceRepository
from skylize.events.memory_bus import InMemoryEventBus
from skylize.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionError,
    ToolPermissionDenied,
    ToolSpendDeferredToHuman,
    ToolSpendDenied,
    ToolSpendHardDenied,
    ToolSpendUnavailable,
)
from skylize.tools.proxy import ToolProxy
from skylize.tools.registry import ToolRegistry

ORG = "org_test"
PRINCIPAL = "devon"
AGENT = "spend_test_agent"
SPEND_TOOL = "test.spend"
FREE_TOOL = "test.free"


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _SpendIn(BaseModel):
    amount_minor: int


class _SpendOut(BaseModel):
    ok: bool


class FakeSpendRepo:
    """Reproduces `PostgresSpendRepository`'s contract, not its SQL.

    Specifically: `try_reserve` returns None (rather than raising) when the
    ceiling would be breached, which is what makes `SpendLedger.reserve` re-read
    the envelope to build its denial reason.
    """

    def __init__(
        self,
        *,
        ceiling_minor: int = 10_000,
        over_ceiling_behavior: str = "hard_deny",
        envelope: bool = True,
    ) -> None:
        self.envelope_id = uuid4()
        self.ceiling_minor = ceiling_minor
        self.reserved_minor = 0
        self.spent_minor = 0
        self.over_ceiling_behavior = over_ceiling_behavior
        self._has_envelope = envelope
        self._reservations: dict[UUID, Reservation] = {}
        self.commits: list[tuple[UUID, int]] = []
        self.releases: list[UUID] = []
        self.release_raises = False

    async def try_reserve(
        self, *, org_id, principal_id, amount_minor, idempotency_key,
        correlation_id, governance_token_id, now, expires_at,
    ) -> Reservation | None:
        if not self._has_envelope:
            return None
        if self.spent_minor + self.reserved_minor + amount_minor > self.ceiling_minor:
            return None
        self.reserved_minor += amount_minor
        res = Reservation(
            reservation_id=uuid4(), envelope_id=self.envelope_id, org_id=org_id,
            idempotency_key=idempotency_key, amount_minor=amount_minor,
            correlation_id=correlation_id, governance_token_id=governance_token_id,
            state="held", created_at=now, expires_at=expires_at,
        )
        self._reservations[res.reservation_id] = res
        return res

    async def commit(self, *, org_id, reservation_id, actual_minor, now) -> None:
        res = self._reservations.get(reservation_id)
        if res is None or res.state != "held":
            return
        actual = min(actual_minor, res.amount_minor)
        self.reserved_minor -= res.amount_minor
        self.spent_minor += actual
        self._reservations[reservation_id] = res.model_copy(
            update={"state": "committed", "committed_minor": actual}
        )
        self.commits.append((reservation_id, actual))

    async def release(self, *, org_id, reservation_id, now) -> None:
        if self.release_raises:
            raise RuntimeError("ledger unreachable")
        res = self._reservations.get(reservation_id)
        if res is None or res.state != "held":
            return
        self.reserved_minor -= res.amount_minor
        self._reservations[reservation_id] = res.model_copy(update={"state": "released"})
        self.releases.append(reservation_id)

    async def get_envelope(self, *, org_id, principal_id, now) -> SpendEnvelope | None:
        if not self._has_envelope:
            return None
        return SpendEnvelope(
            envelope_id=self.envelope_id, org_id=org_id, principal_id=principal_id,
            currency="USD", ceiling_minor=self.ceiling_minor,
            reserved_minor=self.reserved_minor, spent_minor=self.spent_minor,
            period_start=now - timedelta(days=1), period_end=now + timedelta(days=30),
            over_ceiling_behavior=self.over_ceiling_behavior,
        )

    async def sweep_expired(self, *, now, limit: int = 500) -> int:
        return 0

    def held(self) -> list[Reservation]:
        return [r for r in self._reservations.values() if r.state == "held"]


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def _contract() -> AgentContract:
    return AgentContract(
        agent_id=AGENT,
        agent_role="Spend test",
        authority_level="worker",
        department="engineering",
        input_schema="skylize.runtime.agent_runner.AgentRunInput",
        output_schema="skylize.runtime.agent_runner.AgentRunResult",
        allowed_tools=[
            ToolGrant(tool_id=SPEND_TOOL, purpose="test"),
            ToolGrant(tool_id=FREE_TOOL, purpose="test"),
        ],
        max_token_budget=8_000,
        max_execution_time_seconds=60,
        escalation_path=["human_owner"],
        failure_mode=FailureMode.FALLBACK_DEGRADED,
        memory_read_access=[],
        memory_write_access=[],
    )


def _registry(*, fail: bool = False) -> ToolRegistry:
    async def spend_handler(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        if fail:
            raise RuntimeError("vendor API exploded")
        return _SpendOut(ok=True)

    async def free_handler(inp: BaseModel, ctx: ToolContext) -> BaseModel:
        return _SpendOut(ok=True)

    return ToolRegistry([
        ToolDefinition(
            tool_id=SPEND_TOOL, name="Spend", description="spends money",
            input_schema=_SpendIn, output_schema=_SpendOut, category="integration",
            handler=spend_handler,
            spend={"currency": "USD", "amount_field": "amount_minor"},
        ),
        ToolDefinition(
            tool_id=FREE_TOOL, name="Free", description="spends nothing",
            input_schema=_SpendIn, output_schema=_SpendOut, category="compute",
            handler=free_handler,
        ),
    ])


def _authority(*, with_principal: bool = True):
    bus = InMemoryEventBus()
    audit = AuditService(bus, InMemoryAuditRepository())
    provider = None
    if with_principal:
        repo = InMemoryPrincipalRepository()
        repo.add_principal(
            Principal(
                principal_id=PRINCIPAL, org_id=ORG, display_name="Devon",
                authority_level="manager",
            )
        )
        for scope in (SPEND_TOOL, FREE_TOOL):
            repo.add_grant(
                org_id=ORG, principal_id=PRINCIPAL,
                grant=Grant(
                    scope=scope, source=GrantSource.POSITION,
                    valid_from=datetime.now(timezone.utc) - timedelta(days=1),
                ),
            )
        provider = PrincipalAuthorityService(repo)
    authority = GovernanceAuthority.build(
        repo=InMemoryGovernanceRepository(), audit=audit, bus=bus,
        registry=MVP_REGISTRY, settings=Settings(backend="memory"),
        principal_authority=provider,
    )
    return authority, bus, audit


def _proxy(authority, audit, repo: FakeSpendRepo | None, *, fail: bool = False) -> ToolProxy:
    return ToolProxy(
        registry=_registry(fail=fail), audit=audit,
        public_key=authority.public_key,
        live_state_for=authority.live_state_checker,
        spend_ledger=SpendLedger(repo) if repo is not None else None,
    )


async def _v11_token(authority, contract, corr):
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
# Happy path: reserve -> commit
# --------------------------------------------------------------------------- #


async def test_successful_spend_call_commits_the_hold() -> None:
    authority, bus, audit = _authority()
    repo = FakeSpendRepo(ceiling_minor=10_000)
    proxy = _proxy(authority, audit, repo)
    contract = _contract()
    corr = uuid4()
    token = await _v11_token(authority, contract, corr)

    result = await proxy.invoke(
        tool_id=SPEND_TOOL, input_data={"amount_minor": 2_500},
        governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
    )

    assert result.output_json() == {"ok": True}
    assert repo.spent_minor == 2_500, "actual spend must land in spent_minor"
    assert repo.reserved_minor == 0, "the hold must not survive the call"
    assert repo.held() == [], "no reservation may remain in 'held'"
    assert len(repo.commits) == 1


async def test_non_spend_capable_tool_never_touches_the_ledger() -> None:
    """The overwhelming majority of tools. Their path must be byte-for-byte
    unchanged — no hold, no ledger round-trip, no new failure mode."""
    authority, bus, audit = _authority()
    repo = FakeSpendRepo()
    proxy = _proxy(authority, audit, repo)
    contract = _contract()
    corr = uuid4()
    token = await _v11_token(authority, contract, corr)

    await proxy.invoke(
        tool_id=FREE_TOOL, input_data={"amount_minor": 999_999},
        governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
    )

    assert repo.commits == [] and repo.releases == []
    assert repo.reserved_minor == 0 and repo.spent_minor == 0


# --------------------------------------------------------------------------- #
# The leak case: a failure after a successful reserve
# --------------------------------------------------------------------------- #


async def test_handler_failure_releases_the_reservation() -> None:
    """The case the design flagged: reserve succeeds, then the tool blows up.

    Without an explicit release the hold survives until `sweep_expired` reclaims
    it, and the customer loses spend capacity for the full hold TTL for an action
    that never happened.
    """
    authority, bus, audit = _authority()
    repo = FakeSpendRepo(ceiling_minor=10_000)
    proxy = _proxy(authority, audit, repo, fail=True)
    contract = _contract()
    corr = uuid4()
    token = await _v11_token(authority, contract, corr)

    with pytest.raises(ToolExecutionError):
        await proxy.invoke(
            tool_id=SPEND_TOOL, input_data={"amount_minor": 4_000},
            governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
        )

    assert repo.reserved_minor == 0, "the hold leaked past the failed call"
    assert repo.spent_minor == 0, "a failed call must not spend"
    assert len(repo.releases) == 1
    assert repo.commits == []


async def test_budget_is_reusable_after_a_failed_call() -> None:
    """The observable consequence of the release: the freed budget is spendable
    again immediately, not 15 minutes later."""
    authority, bus, audit = _authority()
    repo = FakeSpendRepo(ceiling_minor=5_000)
    contract = _contract()
    corr = uuid4()
    token = await _v11_token(authority, contract, corr)

    failing = _proxy(authority, audit, repo, fail=True)
    with pytest.raises(ToolExecutionError):
        await failing.invoke(
            tool_id=SPEND_TOOL, input_data={"amount_minor": 5_000},
            governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
        )

    # The whole ceiling was held a moment ago; it must be free again now.
    working = _proxy(authority, audit, repo)
    await working.invoke(
        tool_id=SPEND_TOOL, input_data={"amount_minor": 5_000},
        governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
    )
    assert repo.spent_minor == 5_000


async def test_release_failure_does_not_mask_the_tool_error() -> None:
    """A ledger hiccup while unwinding must not replace the error the caller
    actually needs to see. The hold is not lost — the sweeper reclaims it."""
    authority, bus, audit = _authority()
    repo = FakeSpendRepo(ceiling_minor=10_000)
    repo.release_raises = True
    proxy = _proxy(authority, audit, repo, fail=True)
    contract = _contract()
    corr = uuid4()
    token = await _v11_token(authority, contract, corr)

    with pytest.raises(ToolExecutionError, match="vendor API exploded"):
        await proxy.invoke(
            tool_id=SPEND_TOOL, input_data={"amount_minor": 1_000},
            governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
        )


# --------------------------------------------------------------------------- #
# hard_deny vs defer_to_human — the distinction, positively covered
# --------------------------------------------------------------------------- #


async def test_hard_deny_raises_its_own_type_and_does_not_dispatch() -> None:
    authority, bus, audit = _authority()
    repo = FakeSpendRepo(ceiling_minor=1_000, over_ceiling_behavior="hard_deny")
    proxy = _proxy(authority, audit, repo)
    contract = _contract()
    corr = uuid4()
    token = await _v11_token(authority, contract, corr)

    with pytest.raises(ToolSpendHardDenied) as excinfo:
        await proxy.invoke(
            tool_id=SPEND_TOOL, input_data={"amount_minor": 5_000},
            governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
        )

    assert excinfo.value.defer_to_human is False
    assert excinfo.value.failed_stage == "budget"
    # Not the deferral type — the whole point of the split.
    assert not isinstance(excinfo.value, ToolSpendDeferredToHuman)
    assert repo.reserved_minor == 0 and repo.spent_minor == 0
    assert any("budget" in r for r in _denied_reasons(bus)), "denial must be audited"


async def test_defer_to_human_raises_a_distinct_type() -> None:
    authority, bus, audit = _authority()
    repo = FakeSpendRepo(ceiling_minor=1_000, over_ceiling_behavior="defer_to_human")
    proxy = _proxy(authority, audit, repo)
    contract = _contract()
    corr = uuid4()
    token = await _v11_token(authority, contract, corr)

    with pytest.raises(ToolSpendDeferredToHuman) as excinfo:
        await proxy.invoke(
            tool_id=SPEND_TOOL, input_data={"amount_minor": 5_000},
            governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
        )

    assert excinfo.value.defer_to_human is True
    assert not isinstance(excinfo.value, ToolSpendHardDenied)
    assert repo.spent_minor == 0


def test_the_two_denials_are_not_interchangeable() -> None:
    """Guards the split structurally: neither type may catch the other.

    If one were ever made a subclass of the other, `except ToolSpendHardDenied`
    would start swallowing deferrals (or vice versa) and the envelope's
    `over_ceiling_behavior` would stop meaning anything at the call site.
    """
    assert not issubclass(ToolSpendHardDenied, ToolSpendDeferredToHuman)
    assert not issubclass(ToolSpendDeferredToHuman, ToolSpendHardDenied)
    # ...while both still route through the shared denial path.
    assert issubclass(ToolSpendHardDenied, ToolSpendDenied)
    assert issubclass(ToolSpendDeferredToHuman, ToolSpendDenied)
    assert issubclass(ToolSpendDenied, ToolPermissionDenied)


# --------------------------------------------------------------------------- #
# Fail-closed
# --------------------------------------------------------------------------- #


async def test_spend_capable_tool_without_a_ledger_fails_closed() -> None:
    """An unenforced ceiling is worse than no ceiling: it reads as enforced."""
    authority, bus, audit = _authority()
    proxy = _proxy(authority, audit, None)
    contract = _contract()
    corr = uuid4()
    token = await _v11_token(authority, contract, corr)

    with pytest.raises(ToolSpendUnavailable, match="no spend ledger"):
        await proxy.invoke(
            tool_id=SPEND_TOOL, input_data={"amount_minor": 100},
            governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
        )
    assert any("budget" in r for r in _denied_reasons(bus))


async def test_v10_token_cannot_spend() -> None:
    """A v1.0 autonomous token names no human to charge (contracts/base.py:239).
    There is no org-level fallback envelope — that is the design."""
    authority, bus, audit = _authority(with_principal=False)
    repo = FakeSpendRepo()
    proxy = _proxy(authority, audit, repo)
    contract = _contract()
    corr = uuid4()
    token = await authority.mint(contract, org_id=ORG, correlation_id=corr)
    assert token.on_behalf_of is None

    with pytest.raises(ToolSpendUnavailable, match="on_behalf_of"):
        await proxy.invoke(
            tool_id=SPEND_TOOL, input_data={"amount_minor": 100},
            governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
        )
    assert repo.reserved_minor == 0


async def test_missing_envelope_fails_closed_not_open() -> None:
    """Absence of a budget is never unlimited budget."""
    authority, bus, audit = _authority()
    repo = FakeSpendRepo(envelope=False)
    proxy = _proxy(authority, audit, repo)
    contract = _contract()
    corr = uuid4()
    token = await _v11_token(authority, contract, corr)

    with pytest.raises(ToolSpendUnavailable):
        await proxy.invoke(
            tool_id=SPEND_TOOL, input_data={"amount_minor": 100},
            governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
        )
    assert repo.spent_minor == 0


@pytest.mark.parametrize("amount", [0, -50])
async def test_non_positive_amount_is_refused(amount: int) -> None:
    authority, bus, audit = _authority()
    repo = FakeSpendRepo()
    proxy = _proxy(authority, audit, repo)
    contract = _contract()
    corr = uuid4()
    token = await _v11_token(authority, contract, corr)

    with pytest.raises(ToolSpendUnavailable, match="positive integer"):
        await proxy.invoke(
            tool_id=SPEND_TOOL, input_data={"amount_minor": amount},
            governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
        )
    assert repo.reserved_minor == 0


async def test_reservation_carries_the_governance_token_id() -> None:
    """The audit question 'which token authorised this spend?' must be answerable
    from the ledger row alone."""
    authority, bus, audit = _authority()
    repo = FakeSpendRepo()
    proxy = _proxy(authority, audit, repo)
    contract = _contract()
    corr = uuid4()
    token = await _v11_token(authority, contract, corr)

    await proxy.invoke(
        tool_id=SPEND_TOOL, input_data={"amount_minor": 100},
        governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
    )
    committed = [r for r in repo._reservations.values() if r.state == "committed"]
    assert len(committed) == 1
    assert committed[0].governance_token_id == token.token_id
    assert committed[0].correlation_id == corr


async def test_each_call_gets_its_own_idempotency_key() -> None:
    """A key shared across two distinct calls would make `try_reserve` return the
    FIRST hold, letting the second call spend against a reservation it never
    made. Distinct calls must therefore never collide."""
    authority, bus, audit = _authority()
    repo = FakeSpendRepo(ceiling_minor=10_000)
    proxy = _proxy(authority, audit, repo)
    contract = _contract()
    corr = uuid4()
    token = await _v11_token(authority, contract, corr)

    for _ in range(3):
        await proxy.invoke(
            tool_id=SPEND_TOOL, input_data={"amount_minor": 1_000},
            governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
        )

    keys = {r.idempotency_key for r in repo._reservations.values()}
    assert len(keys) == 3, "identical calls in one run must not share a hold"
    assert repo.spent_minor == 3_000


async def test_executed_action_is_audited_even_if_the_commit_fails() -> None:
    """A ledger failure must not erase the evidence that the tool ran.

    The side effect is already in the world at this point. If `commit` were
    awaited before the audit write, a raising commit would leave a real action
    with no audit row — the one record an investigator needs.
    """
    authority, bus, audit = _authority()

    class CommitFails(FakeSpendRepo):
        async def commit(self, **kw):
            raise RuntimeError("ledger write failed")

    repo = CommitFails(ceiling_minor=10_000)
    proxy = _proxy(authority, audit, repo)
    contract = _contract()
    corr = uuid4()
    token = await _v11_token(authority, contract, corr)

    with pytest.raises(RuntimeError, match="ledger write failed"):
        await proxy.invoke(
            tool_id=SPEND_TOOL, input_data={"amount_minor": 1_000},
            governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
        )

    successes = [
        e for e in bus.published_of_type("audit.action_recorded")
        if e.payload.action_type == "tool.invoked" and e.payload.result == "success"
    ]
    assert successes, "the executed action left no audit record"
    # The hold must NOT have been released: the action ran, so its budget is real.
    assert repo.reserved_minor == 1_000
    assert repo.releases == []
