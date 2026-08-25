"""The spend ceiling on the tool-call path — REAL Postgres, as the app role.

This file exists for one claim the unit suite cannot make: that the ceiling is
ATOMIC under concurrency. `spend.py:214`

    AND e.spent_minor + e.reserved_minor + $3 <= e.ceiling_minor

is the entire policy, and its correctness is a property of PostgreSQL's
concurrent-UPDATE semantics — a fake repository asserts the invariant it was
written to satisfy and proves nothing about the real one. The specific race the
design flagged is two simultaneous spend attempts against a ceiling only one can
satisfy; `test_two_concurrent_reserves_only_one_wins` closes it and
`test_n_way_concurrent_reserves_never_exceed_the_ceiling` shows it holds at
width, not just for a lucky pair of two.

Read the docstring on `test_two_concurrent_reserves_only_one_wins` before
trusting it: money cannot be overspent here even WITHOUT the WHERE clause,
because migration 0019 carries a table CHECK constraint as an independent second
layer. What these tests actually establish is that the ceiling produces a clean,
correctly-flagged DENIAL under contention rather than a constraint violation.

Everything runs as the non-superuser, non-table-owner `skylize_app` role, so RLS
is genuinely in force (a superuser bypasses it and would prove nothing).

Skipped unless SKYLIZE_TEST_DB_URL and SKYLIZE_TEST_APP_DB_URL are both set.
All amounts are synthetic integer minor units (cents).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from skylize.app.principal.errors import CeilingExceeded
from skylize.app.principal.spend import PostgresSpendRepository, SpendLedger

from .conftest import APP_DB_URL, DB_URL, requires_app_role

pytestmark = pytest.mark.integration

PRINCIPAL = "devon"


async def _seed_envelope(
    *, ceiling_minor: int, over_ceiling_behavior: str = "hard_deny"
) -> str:
    """Create a tenant + one active envelope as the admin role. Returns org_id."""
    import asyncpg

    org = f"org_spend_{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)
    conn = await asyncpg.connect(DB_URL)
    try:
        await conn.execute(
            "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3)",
            org, "spend-test", "https://test.example",
        )
        await conn.execute(
            """INSERT INTO spend_envelope (org_id, principal_id, currency,
                 ceiling_minor, period_start, period_end, over_ceiling_behavior)
               VALUES ($1,$2,'USD',$3,$4,$5,$6)""",
            org, PRINCIPAL, ceiling_minor,
            now - timedelta(days=1), now + timedelta(days=30), over_ceiling_behavior,
        )
    finally:
        await conn.close()
    return org


async def _drop_org(org: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(DB_URL)
    try:
        await conn.execute(
            "DELETE FROM spend_reservation WHERE org_id=$1", org
        )
        await conn.execute("DELETE FROM spend_envelope WHERE org_id=$1", org)
        await conn.execute("DELETE FROM tenants WHERE org_id=$1", org)
    finally:
        await conn.close()


async def _envelope_row(org: str) -> dict:
    """Read the envelope as the ADMIN role — an independent observer, so the
    assertion does not depend on the same RLS path under test."""
    import asyncpg

    conn = await asyncpg.connect(DB_URL)
    try:
        row = await conn.fetchrow(
            "SELECT ceiling_minor, reserved_minor, spent_minor FROM spend_envelope "
            "WHERE org_id=$1 AND principal_id=$2",
            org, PRINCIPAL,
        )
        return dict(row)
    finally:
        await conn.close()


async def _reservation_states(org: str) -> dict[str, int]:
    import asyncpg

    conn = await asyncpg.connect(DB_URL)
    try:
        rows = await conn.fetch(
            "SELECT state, count(*) AS n FROM spend_reservation WHERE org_id=$1 "
            "GROUP BY state",
            org,
        )
        return {r["state"]: r["n"] for r in rows}
    finally:
        await conn.close()


# --------------------------------------------------------------------------- #
# The race
# --------------------------------------------------------------------------- #


@requires_app_role
async def test_two_concurrent_reserves_only_one_wins() -> None:
    """THE flagged race, on real Postgres.

    Ceiling 5000; two simultaneous reserves of 3000. Both read "under ceiling" if
    the check is a read-then-check in Python. Exactly one may win.

    The two attempts run on SEPARATE pooled connections released into a barrier,
    so both are genuinely in flight inside the database at the same time rather
    than being serialised by the event loop.

    WHAT DISCRIMINATES THIS TEST (verified by a mutation control, 2026-08-23):
    running this exact harness against a naive read-then-check repository does
    NOT overspend the envelope — the table CHECK constraint
    `spent_minor + reserved_minor <= ceiling_minor` (migration 0019) is an
    independent second layer that refuses the row. What the naive version does
    instead is lose the race LOUDLY: the second attempt dies with an asyncpg
    CheckViolationError rather than a clean denial.

    So the assertion with teeth here is not "no overspend" — it is that the loser
    is denied by the WHERE clause at spend.py:214 as a well-formed
    `CeilingExceeded` carrying the envelope's `over_ceiling_behavior`. That is the
    difference between a ceiling that DECIDES and a constraint that merely
    crashes. `assert not other` below is what fails on a read-then-check.
    """
    import asyncpg

    org = await _seed_envelope(ceiling_minor=5_000)
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=2, max_size=4)
    try:
        ledger = SpendLedger(PostgresSpendRepository(pool))
        barrier = asyncio.Barrier(2)

        async def attempt(n: int):
            await barrier.wait()  # both cross into the SQL together
            return await ledger.reserve(
                org_id=org, principal_id=PRINCIPAL, amount_minor=3_000,
                idempotency_key=f"race-{org}-{n}", correlation_id=uuid.uuid4(),
            )

        results = await asyncio.gather(
            attempt(1), attempt(2), return_exceptions=True
        )
    finally:
        await pool.close()

    try:
        winners = [r for r in results if not isinstance(r, BaseException)]
        losers = [r for r in results if isinstance(r, CeilingExceeded)]
        other = [
            r for r in results
            if isinstance(r, BaseException) and not isinstance(r, CeilingExceeded)
        ]

        # The discriminating assertion: a read-then-check implementation reaches
        # here with a CheckViolationError from the table constraint instead of a
        # decision. A raised constraint is not a governance decision.
        assert not other, (
            f"the losing reserve did not DECIDE, it crashed: {other!r} — the "
            f"denial must come from the WHERE clause (spend.py:214), not from "
            f"the table CHECK backstop"
        )
        assert len(winners) == 1, (
            f"expected exactly one winner against a 5000 ceiling with two 3000 "
            f"reserves, got {len(winners)} — the ceiling is NOT atomic"
        )
        assert len(losers) == 1
        assert losers[0].defer_to_human is False  # envelope is hard_deny

        row = await _envelope_row(org)
        assert row["reserved_minor"] == 3_000, (
            f"reserved_minor={row['reserved_minor']} — a second hold was admitted"
        )
        assert row["spent_minor"] + row["reserved_minor"] <= row["ceiling_minor"]

        states = await _reservation_states(org)
        assert states.get("held") == 1
    finally:
        await _drop_org(org)


@requires_app_role
async def test_n_way_concurrent_reserves_never_exceed_the_ceiling() -> None:
    """The same guarantee at width: 12 simultaneous reserves of 1000 against a
    ceiling of 5000. Exactly 5 may win, no more — and the sum held must land
    exactly on the ceiling, not near it."""
    import asyncpg

    org = await _seed_envelope(ceiling_minor=5_000)
    attempts = 12
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=attempts, max_size=attempts)
    try:
        ledger = SpendLedger(PostgresSpendRepository(pool))
        barrier = asyncio.Barrier(attempts)

        async def attempt(n: int):
            await barrier.wait()
            return await ledger.reserve(
                org_id=org, principal_id=PRINCIPAL, amount_minor=1_000,
                idempotency_key=f"nway-{org}-{n}", correlation_id=uuid.uuid4(),
            )

        results = await asyncio.gather(
            *(attempt(i) for i in range(attempts)), return_exceptions=True
        )
    finally:
        await pool.close()

    try:
        winners = [r for r in results if not isinstance(r, BaseException)]
        denials = [r for r in results if isinstance(r, CeilingExceeded)]
        other = [
            r for r in results
            if isinstance(r, BaseException) and not isinstance(r, CeilingExceeded)
        ]

        assert not other, f"unexpected error: {other!r}"
        assert len(winners) == 5, f"expected exactly 5 winners, got {len(winners)}"
        assert len(denials) == attempts - 5

        row = await _envelope_row(org)
        assert row["reserved_minor"] == 5_000
        assert row["spent_minor"] + row["reserved_minor"] == row["ceiling_minor"]
    finally:
        await _drop_org(org)


# --------------------------------------------------------------------------- #
# Lifecycle against the real adapter
# --------------------------------------------------------------------------- #


@requires_app_role
async def test_reserve_commit_moves_hold_into_spent() -> None:
    import asyncpg

    org = await _seed_envelope(ceiling_minor=10_000)
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=2, max_size=4)
    try:
        ledger = SpendLedger(PostgresSpendRepository(pool))
        res = await ledger.reserve(
            org_id=org, principal_id=PRINCIPAL, amount_minor=2_500,
            idempotency_key=f"commit-{org}", correlation_id=uuid.uuid4(),
        )
        mid = await _envelope_row(org)
        assert mid["reserved_minor"] == 2_500 and mid["spent_minor"] == 0

        await ledger.commit(
            org_id=org, reservation_id=res.reservation_id, actual_minor=2_500
        )
    finally:
        await pool.close()

    try:
        row = await _envelope_row(org)
        assert row["reserved_minor"] == 0, "the hold must not survive the commit"
        assert row["spent_minor"] == 2_500
        assert (await _reservation_states(org)).get("committed") == 1
    finally:
        await _drop_org(org)


@requires_app_role
async def test_release_returns_the_hold_to_the_ceiling() -> None:
    """The failure path, against real SQL: a released hold frees budget for the
    next call immediately rather than waiting for `sweep_expired`."""
    import asyncpg

    org = await _seed_envelope(ceiling_minor=5_000)
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=2, max_size=4)
    try:
        ledger = SpendLedger(PostgresSpendRepository(pool))
        res = await ledger.reserve(
            org_id=org, principal_id=PRINCIPAL, amount_minor=5_000,
            idempotency_key=f"rel-{org}", correlation_id=uuid.uuid4(),
        )
        # The whole ceiling is held; a second reserve must be refused.
        with pytest.raises(CeilingExceeded):
            await ledger.reserve(
                org_id=org, principal_id=PRINCIPAL, amount_minor=1,
                idempotency_key=f"rel-blocked-{org}", correlation_id=uuid.uuid4(),
            )

        await ledger.release(org_id=org, reservation_id=res.reservation_id)
        assert (await _envelope_row(org))["reserved_minor"] == 0

        # ...and the budget is spendable again right away.
        again = await ledger.reserve(
            org_id=org, principal_id=PRINCIPAL, amount_minor=5_000,
            idempotency_key=f"rel-again-{org}", correlation_id=uuid.uuid4(),
        )
        assert again.amount_minor == 5_000
    finally:
        await pool.close()
        await _drop_org(org)


@requires_app_role
async def test_get_envelope_is_visible_to_the_rls_subject_role() -> None:
    """Regression: `get_envelope` set its RLS GUC with `is_local=true` OUTSIDE a
    transaction, so the setting was discarded before the SELECT ran and the row
    was invisible to `skylize_app`. That made every ceiling denial surface as
    `EnvelopeNotFound` and left `CeilingExceeded.defer_to_human` — the entire
    hard_deny/defer_to_human distinction — unreachable in production.

    It also selected `*` into a model that forbids extras (`created_at`).
    """
    import asyncpg

    org = await _seed_envelope(ceiling_minor=7_500, over_ceiling_behavior="defer_to_human")
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=1, max_size=2)
    try:
        repo = PostgresSpendRepository(pool)
        env = await repo.get_envelope(
            org_id=org, principal_id=PRINCIPAL, now=datetime.now(timezone.utc)
        )
        assert env is not None, "envelope invisible to the RLS-subject app role"
        assert env.ceiling_minor == 7_500
        assert env.over_ceiling_behavior == "defer_to_human"
        assert env.available_minor == 7_500
    finally:
        await pool.close()
        await _drop_org(org)


@requires_app_role
async def test_defer_to_human_behavior_survives_the_round_trip() -> None:
    """The hard_deny/defer_to_human branch reads `over_ceiling_behavior` off the
    envelope, so the flag has to come back correctly from real SQL — not just
    from a fake that was constructed with it."""
    import asyncpg

    org = await _seed_envelope(ceiling_minor=1_000, over_ceiling_behavior="defer_to_human")
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=1, max_size=2)
    try:
        ledger = SpendLedger(PostgresSpendRepository(pool))
        with pytest.raises(CeilingExceeded) as excinfo:
            await ledger.reserve(
                org_id=org, principal_id=PRINCIPAL, amount_minor=9_999,
                idempotency_key=f"defer-{org}", correlation_id=uuid.uuid4(),
            )
        assert excinfo.value.defer_to_human is True
    finally:
        await pool.close()
        await _drop_org(org)


@requires_app_role
async def test_hard_deny_behavior_survives_the_round_trip() -> None:
    import asyncpg

    org = await _seed_envelope(ceiling_minor=1_000, over_ceiling_behavior="hard_deny")
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=1, max_size=2)
    try:
        ledger = SpendLedger(PostgresSpendRepository(pool))
        with pytest.raises(CeilingExceeded) as excinfo:
            await ledger.reserve(
                org_id=org, principal_id=PRINCIPAL, amount_minor=9_999,
                idempotency_key=f"hard-{org}", correlation_id=uuid.uuid4(),
            )
        assert excinfo.value.defer_to_human is False
    finally:
        await pool.close()
        await _drop_org(org)


@requires_app_role
async def test_tenant_isolation_holds_on_the_spend_tables() -> None:
    """One org must not reserve against another org's envelope, proven as the
    RLS-subject role."""
    import asyncpg

    from skylize.app.principal.errors import EnvelopeNotFound

    org_a = await _seed_envelope(ceiling_minor=10_000)
    org_b = await _seed_envelope(ceiling_minor=10_000)
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=1, max_size=2)
    try:
        ledger = SpendLedger(PostgresSpendRepository(pool))
        # org_b's ledger call naming org_a's principal must not reach org_a's row.
        with pytest.raises(EnvelopeNotFound):
            await ledger.reserve(
                org_id=org_b, principal_id="somebody-else",
                amount_minor=100, idempotency_key=f"iso-{org_b}",
                correlation_id=uuid.uuid4(),
            )
        assert (await _envelope_row(org_a))["reserved_minor"] == 0
    finally:
        await pool.close()
        await _drop_org(org_a)
        await _drop_org(org_b)


# --------------------------------------------------------------------------- #
# End to end: the ceiling enforced through ToolProxy.invoke, on real Postgres
# --------------------------------------------------------------------------- #


@requires_app_role
async def test_proxy_invoke_enforces_the_ceiling_end_to_end() -> None:
    """The wiring itself, not just the ledger underneath it.

    A spend-capable tool invoked through the real `ToolProxy` with a real v1.1
    governance token: the first call fits the envelope and commits, the second
    breaches it and is refused as `ToolSpendHardDenied` with the tool's handler
    never reached.
    """
    import asyncpg

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
    from skylize.tools.base import ToolContext, ToolDefinition, ToolSpendHardDenied
    from skylize.tools.proxy import ToolProxy
    from skylize.tools.registry import ToolRegistry

    from pydantic import BaseModel

    class _In(BaseModel):
        amount_minor: int

    class _Out(BaseModel):
        ok: bool

    calls: list[int] = []

    async def handler(inp, ctx: ToolContext):
        calls.append(inp.amount_minor)
        return _Out(ok=True)

    org = await _seed_envelope(ceiling_minor=5_000, over_ceiling_behavior="hard_deny")
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=2, max_size=4)
    try:
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
            agent_id="spend_e2e_agent", agent_role="E2E", authority_level="worker",
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
            spend_ledger=SpendLedger(PostgresSpendRepository(pool)),
        )
        corr = uuid.uuid4()
        token = await authority.mint(
            contract, org_id=org, correlation_id=corr,
            on_behalf_of_principal=PRINCIPAL,
        )
        assert token.on_behalf_of is not None

        await proxy.invoke(
            tool_id="test.spend", input_data={"amount_minor": 4_000},
            governance_token=token, contract=contract, org_id=org,
            correlation_id=corr,
        )
        assert calls == [4_000]
        assert (await _envelope_row(org))["spent_minor"] == 4_000

        # 4000 spent of a 5000 ceiling; 2000 more must be refused.
        with pytest.raises(ToolSpendHardDenied):
            await proxy.invoke(
                tool_id="test.spend", input_data={"amount_minor": 2_000},
                governance_token=token, contract=contract, org_id=org,
                correlation_id=corr,
            )
        assert calls == [4_000], "the handler ran despite the ceiling denial"

        row = await _envelope_row(org)
        assert row["spent_minor"] == 4_000
        assert row["reserved_minor"] == 0, "the refused call left a hold behind"
    finally:
        await pool.close()
        await _drop_org(org)
