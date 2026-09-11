"""Reservation-KEY conflicts on the tool-call path — REAL Postgres, as the app role.

`ReservationConflict` (src/skylize/app/principal/errors.py:116) is raised when an
idempotency key is reused for a DIFFERENT amount
(src/skylize/app/principal/spend.py:316-318). Until this suite existed it was
caught nowhere: `ToolProxy._reserve_spend` handled only `CeilingExceeded`
(src/skylize/tools/proxy.py:880) and `EnvelopeNotFound`, so the conflict left
`ToolProxy.invoke` as a bare `BudgetError`, missed the `except ToolError` branch
that turns a refused call into an error `tool_result`
(src/skylize/app/agents/execution.py:981), and faulted the entire agent run.

These tests pin two things that are easy to conflate:

  * a key conflict is a `ToolError`, so the run degrades instead of faulting;
  * it is NOT a `ToolSpendDenied`, so it can never be routed as a ceiling breach.

Both run against real Postgres because the conflict is produced by a real unique
index and the real re-read branch, not by a fake asserting the invariant it was
written to satisfy. Everything runs as the non-superuser, non-table-owner
`skylize_app` role, so RLS is genuinely in force.

Skipped unless SKYLIZE_TEST_DB_URL and SKYLIZE_TEST_APP_DB_URL are both set.
All amounts are synthetic integer minor units (cents).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from skylize.app.principal.errors import ReservationConflict
from skylize.app.principal.spend import PostgresSpendRepository, SpendLedger

from .conftest import APP_DB_URL, requires_app_role
from .test_tool_proxy_spend_pg import (
    PRINCIPAL,
    _drop_org,
    _envelope_row,
    _held_total,
    _seed_envelope,
)

pytestmark = pytest.mark.integration


class _FixedKeyLedger(SpendLedger):
    """The real ledger over the real database, with the idempotency key PINNED.

    `ToolProxy` mints `tool:{tool_id}:{uuid4()}` per call
    (src/skylize/tools/proxy.py:876), so it cannot repeat a key and
    `ReservationConflict` is unreachable through `invoke` today. Pinning the key
    is the ONLY substitution made here, and it is exactly the caller-supplied-key
    shape that replay keying will introduce: the conflict is still detected by
    the real re-read branch (src/skylize/app/principal/spend.py:316-318) against
    the real `spend_reservation_org_id_idempotency_key_key` unique index — by
    Postgres, not by a fake standing in for it.
    """

    def __init__(self, repo: PostgresSpendRepository, *, key: str) -> None:
        super().__init__(repo)
        self._key = key

    async def reserve(self, **kwargs):  # type: ignore[override]
        kwargs["idempotency_key"] = self._key
        return await super().reserve(**kwargs)


class _RecordingContainment:
    """Records containment proposals so a test can prove one did NOT happen."""

    def __init__(self) -> None:
        self.proposals: list[str] = []

    async def propose_containment(
        self, *, org_id: str, breach_reason: str, user_id: str
    ):
        self.proposals.append(breach_reason)

        class _Outcome:
            status = "queued"
            hitl_id = None

        return _Outcome()


async def _spend_proxy(*, org: str, ledger: SpendLedger, calls: list[int]):
    """A real ToolProxy over a real v1.1 token, mirroring the e2e test next door.

    Returns (proxy, contract, token, correlation_id, containment_recorder).
    """
    from pydantic import BaseModel

    from skylize.app.audit.service import AuditService
    from skylize.app.governance import GovernanceAuthority
    from skylize.app.principal.models import Grant, GrantSource, Principal
    from skylize.app.principal.provider import (
        InMemoryPrincipalRepository,
        PrincipalAuthorityService,
    )
    from skylize.config import Settings
    from skylize.contracts.base import AgentContract, FailureMode, ToolGrant
    from skylize.contracts.registry import MVP_REGISTRY
    from skylize.dal.memory import InMemoryAuditRepository, InMemoryGovernanceRepository
    from skylize.events.memory_bus import InMemoryEventBus
    from skylize.tools.base import ToolContext, ToolDefinition
    from skylize.tools.proxy import ToolProxy
    from skylize.tools.registry import ToolRegistry

    class _In(BaseModel):
        amount_minor: int

    class _Out(BaseModel):
        ok: bool

    async def handler(inp: _In, ctx: ToolContext) -> _Out:
        calls.append(inp.amount_minor)
        return _Out(ok=True)

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
            scope="test.spend", source=GrantSource.POSITION,
            valid_from=datetime.now(timezone.utc) - timedelta(days=1),
        ),
    )
    authority = GovernanceAuthority.build(
        repo=InMemoryGovernanceRepository(), audit=audit, bus=bus,
        registry=MVP_REGISTRY, settings=Settings(backend="memory"),
        principal_authority=PrincipalAuthorityService(prepo),
    )
    contract = AgentContract(
        agent_id="spend_conflict_agent", agent_role="E2E", authority_level="worker",
        department="engineering",
        input_schema="skylize.runtime.agent_runner.AgentRunInput",
        output_schema="skylize.runtime.agent_runner.AgentRunResult",
        allowed_tools=[ToolGrant(tool_id="test.spend", purpose="test")],
        max_token_budget=8_000, max_execution_time_seconds=60,
        escalation_path=["human_owner"],
        failure_mode=FailureMode.FALLBACK_DEGRADED,
        memory_read_access=[], memory_write_access=[],
    )
    registry = ToolRegistry([
        ToolDefinition(
            tool_id="test.spend", name="Spend", description="spends money",
            input_schema=_In, output_schema=_Out, category="integration",
            handler=handler,
            spend={"currency": "USD", "amount_field": "amount_minor"},
        )
    ])
    proxy = ToolProxy(
        registry=registry, audit=audit, public_key=authority.public_key,
        live_state_for=authority.live_state_checker,
        spend_ledger=ledger,
    )
    containment = _RecordingContainment()
    proxy.set_containment_trigger(containment)

    corr = uuid.uuid4()
    token = await authority.mint(
        contract, org_id=org, correlation_id=corr,
        on_behalf_of_principal=PRINCIPAL,
    )
    assert token.on_behalf_of is not None
    return proxy, contract, token, corr, containment


@requires_app_role
async def test_the_ledger_raises_conflict_only_when_the_amount_changed() -> None:
    """The precondition, stated against real Postgres.

    A repeated key with the SAME amount is an ordinary idempotent retry and must
    return the ORIGINAL hold. Only a repeated key with a DIFFERENT amount is a
    `ReservationConflict`. Conflating the two would turn every ordinary retry
    into an error, so the boundary is pinned independently of how the proxy maps
    it.
    """
    import asyncpg

    org = await _seed_envelope(ceiling_minor=100_000)
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=1, max_size=3)
    try:
        ledger = SpendLedger(PostgresSpendRepository(pool))
        key = f"tool:test.spend:{uuid.uuid4()}"

        first = await ledger.reserve(
            org_id=org, principal_id=PRINCIPAL, amount_minor=1_000,
            idempotency_key=key, correlation_id=uuid.uuid4(),
        )

        # Same key, same amount -> the ORIGINAL hold, not an error.
        retry = await ledger.reserve(
            org_id=org, principal_id=PRINCIPAL, amount_minor=1_000,
            idempotency_key=key, correlation_id=uuid.uuid4(),
        )
        assert retry.reservation_id == first.reservation_id
        assert await _held_total(org) == 1_000, "the idempotent retry placed a second hold"

        # Same key, DIFFERENT amount -> refused, and never silently reused.
        with pytest.raises(ReservationConflict) as ei:
            await ledger.reserve(
                org_id=org, principal_id=PRINCIPAL, amount_minor=2_500,
                idempotency_key=key, correlation_id=uuid.uuid4(),
            )
        assert "2500" in str(ei.value)
        assert await _held_total(org) == 1_000, "the refused call left a second hold"
    finally:
        await pool.close()
        await _drop_org(org)


@requires_app_role
async def test_a_key_conflict_surfaces_as_a_tool_error_not_a_faulted_run() -> None:
    """The fix. A reservation-key conflict must not fault the agent run.

    Before this change the underlying `ReservationConflict` escaped `invoke` as a
    bare `BudgetError` and bypassed `except ToolError`
    (src/skylize/app/agents/execution.py:981). It must now arrive as
    `ToolSpendKeyConflict`, which IS a `ToolError` — that is the whole substance
    of the change — while remaining outside the ceiling taxonomy.
    """
    import asyncpg

    from skylize.tools.base import ToolError, ToolSpendDenied, ToolSpendKeyConflict

    calls: list[int] = []
    org = await _seed_envelope(ceiling_minor=100_000)
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=1, max_size=3)
    proxy = None
    try:
        proxy, contract, token, corr, containment = await _spend_proxy(
            org=org, calls=calls,
            ledger=_FixedKeyLedger(
                PostgresSpendRepository(pool), key=f"tool:test.spend:{uuid.uuid4()}"
            ),
        )

        await proxy.invoke(
            tool_id="test.spend", input_data={"amount_minor": 1_000},
            governance_token=token, contract=contract, org_id=org,
            correlation_id=corr,
        )
        assert calls == [1_000]

        # Same pinned key, a DIFFERENT amount: the ledger refuses the KEY.
        with pytest.raises(ToolSpendKeyConflict) as ei:
            await proxy.invoke(
                tool_id="test.spend", input_data={"amount_minor": 2_500},
                governance_token=token, contract=contract, org_id=org,
                correlation_id=corr,
            )
        exc = ei.value

        # THE fix: caught by `except ToolError`, so the run degrades to an error
        # tool_result instead of faulting.
        assert isinstance(exc, ToolError)
        # Distinct from a ceiling breach by TYPE, not by parsing a reason string.
        assert not isinstance(exc, ToolSpendDenied), (
            "a key conflict was collapsed into the ceiling taxonomy"
        )
        # ...and therefore carries no routing flag a caller could act on. A
        # caller-side idempotency fault must never reach a human approval queue
        # dressed as an overspend.
        assert not hasattr(exc, "defer_to_human")
        assert exc.failed_stage == "reservation"

        assert calls == [1_000], "the handler ran despite the key conflict"

        # Drain first, or this negative proves nothing: a scheduled-but-unfinished
        # proposal would leave the list empty and the assertion would pass for the
        # wrong reason. After draining, empty means none was ever scheduled.
        await proxy.drain_containment_tasks()
        assert containment.proposals == [], (
            "a repeated key proposed a containment; nothing about this "
            "customer's spending is known to be wrong"
        )
        row = await _envelope_row(org)
        assert row["reserved_minor"] == 0, "the refused call left a hold behind"
        assert row["spent_minor"] == 1_000, "the refused call moved money"
    finally:
        if proxy is not None:
            await proxy.drain_containment_tasks()
        await pool.close()
        await _drop_org(org)


@requires_app_role
async def test_a_ceiling_breach_still_maps_to_its_own_type_and_fires_containment() -> None:
    """The positive control for the test above.

    Same proxy shape, same tool — only the reason for refusal differs. A genuine
    ceiling breach must still be a `ToolSpendDenied` carrying `defer_to_human`,
    and must still fire the containment auto-hook. Without this, "the key
    conflict is distinct" could pass by breaking the ceiling path rather than by
    separating the two.
    """
    import asyncpg

    from skylize.tools.base import (
        ToolSpendDenied,
        ToolSpendHardDenied,
        ToolSpendKeyConflict,
    )

    calls: list[int] = []
    org = await _seed_envelope(ceiling_minor=5_000, over_ceiling_behavior="hard_deny")
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=1, max_size=3)
    proxy = None
    try:
        proxy, contract, token, corr, containment = await _spend_proxy(
            org=org, calls=calls, ledger=SpendLedger(PostgresSpendRepository(pool)),
        )

        with pytest.raises(ToolSpendHardDenied) as ei:
            await proxy.invoke(
                tool_id="test.spend", input_data={"amount_minor": 9_000},
                governance_token=token, contract=contract, org_id=org,
                correlation_id=corr,
            )
        exc = ei.value
        assert isinstance(exc, ToolSpendDenied)
        assert not isinstance(exc, ToolSpendKeyConflict)
        assert exc.defer_to_human is False
        assert exc.failed_stage == "budget"

        # Drain BEFORE asserting. `_schedule_containment` hands the proposal to
        # the event loop and deliberately does not await it (proxy.py:635-645),
        # so the caller gets its denial before the proposal finishes. Asserting
        # without draining races that task and fails intermittently.
        await proxy.drain_containment_tasks()

        # The auto-hook fires for a breach and, per the test above, not for a key
        # conflict. That asymmetry is the point of mapping them distinctly.
        assert len(containment.proposals) == 1
        assert "spend ceiling breached" in containment.proposals[0]
        assert calls == []
    finally:
        if proxy is not None:
            await proxy.drain_containment_tasks()
        await pool.close()
        await _drop_org(org)
