"""Replay behaviour per reservation STATE — REAL Postgres, as the app role.

The four states are not three (`docs/architecture/spend_reservation_replay_
semantics.md` section 1), and each answers a replay differently:

  | state       | a replay must...                                        |
  |-------------|---------------------------------------------------------|
  | `held`      | not re-execute, not place a second hold -> refuse       |
  | `committed` | not re-execute, not re-commit -> return recorded result |
  | `released`  | be able to place a real hold and spend FOR REAL         |
  | `expired`   | likewise                                                |

The first two are enforced by migration 0028's partial unique index, whose
predicate is exactly `state IN ('held','committed')`; the last two are enforced
by that same predicate NOT covering them, so `find_by_replay_key` cannot see
them and the caller proceeds to a genuine reservation. That is the whole design:
the rule lives in the database rather than in application logic that can be
forgotten, so these tests drive it through real SQL rather than a fake.

Settling a released or expired replay to zero, or reusing its settled row, would
UNDER-count a spend that subsequently succeeds — worse than the double-count
this work exists to fix (section 6). The `released`/`expired` cases below are
what pin that.

Everything runs as the non-superuser, non-table-owner `skylize_app` role, so RLS
is genuinely in force. Skipped unless SKYLIZE_TEST_DB_URL and
SKYLIZE_TEST_APP_DB_URL are both set.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from skylize.app.principal.spend import PostgresSpendRepository, SpendLedger
from skylize.tools.base import ToolContext

from .conftest import APP_DB_URL, DB_URL, requires_app_role
from .test_tool_proxy_reservation_conflict_pg import _spend_proxy
from .test_tool_proxy_spend_pg import PRINCIPAL, _drop_org, _envelope_row, _seed_envelope

pytestmark = pytest.mark.integration

HITL_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
TOOL_USE_ID = "toolu_replay_fixture"


def _replay_key_for(hitl_id: uuid.UUID, tool_use_id: str) -> uuid.UUID:
    """Derive the key the way production does, via ToolContext itself.

    Deliberately NOT a re-implementation of the uuid5 derivation: a test that
    recomputed it independently would keep passing if the real derivation
    changed underneath it.
    """
    key = ToolContext(
        org_id="o", agent_id="a", correlation_id=uuid.uuid4(),
        hitl_id=hitl_id, tool_use_id=tool_use_id, is_hitl_resumption=True,
    ).replay_key()
    assert key is not None
    return key


async def _reservation_rows(org: str) -> list[dict]:
    """Read reservations as the ADMIN role — an independent observer, so the
    assertion does not depend on the same RLS path under test."""
    import asyncpg

    conn = await asyncpg.connect(DB_URL)
    try:
        rows = await conn.fetch(
            "SELECT reservation_id, state, amount_minor, committed_minor, "
            "       replay_key, result_snapshot "
            "  FROM spend_reservation WHERE org_id=$1 ORDER BY created_at",
            org,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def _expire_hold(org: str, reservation_id: uuid.UUID) -> None:
    """Put a hold into `expired`, the state the sweeper puts it in.

    Written as the admin role rather than by calling `sweep_expired`, which
    cannot reach the row: `sweep_expired` never sets the RLS GUC its sibling
    methods set, so as the RLS-subject `skylize_app` role the `tenant_isolation`
    policy filters every candidate and it reclaims nothing. That is a real defect
    in the sweeper, it is owned by the `fix/sweep-expired-rls-org-binding` branch
    rather than this one, and hanging this suite off it would make these
    assertions fail for a reason that has nothing to do with replay. What is
    under test here is what a replay does when it meets an `expired` row, so the
    row is put into that state directly.
    """
    import asyncpg

    conn = await asyncpg.connect(DB_URL)
    try:
        await conn.execute(
            "UPDATE spend_reservation "
            "   SET state = 'expired', settled_at = $2, expires_at = $2 "
            " WHERE reservation_id = $1",
            reservation_id, datetime.now(timezone.utc) - timedelta(hours=1),
        )
        # The sweeper also returns the hold to the envelope; mirror that, or the
        # envelope keeps reserving budget for a hold that no longer exists.
        await conn.execute(
            "UPDATE spend_envelope e "
            "   SET reserved_minor = e.reserved_minor - r.amount_minor "
            "  FROM spend_reservation r "
            " WHERE r.reservation_id = $1 AND e.envelope_id = r.envelope_id",
            reservation_id,
        )
    finally:
        await conn.close()


async def _invoke_replay(proxy, *, contract, token, org: str, corr, amount: int):
    """Invoke as a RESUMED HITL turn — the only shape with a replay key."""
    return await proxy.invoke(
        tool_id="test.spend", input_data={"amount_minor": amount},
        governance_token=token, contract=contract, org_id=org,
        correlation_id=corr,
        hitl_id=HITL_ID, tool_use_id=TOOL_USE_ID, is_hitl_resumption=True,
    )


@requires_app_role
async def test_a_committed_replay_returns_the_recorded_result_without_re_executing() -> None:
    """`committed`: the action happened once. The replay must not make it happen twice."""
    import asyncpg

    calls: list[int] = []
    org = await _seed_envelope(ceiling_minor=100_000)
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=1, max_size=3)
    proxy = None
    try:
        proxy, contract, token, corr, _ = await _spend_proxy(
            org=org, calls=calls, ledger=SpendLedger(PostgresSpendRepository(pool)),
        )

        first = await _invoke_replay(
            proxy, contract=contract, token=token, org=org, corr=corr, amount=3_000
        )
        assert calls == [3_000]

        rows = await _reservation_rows(org)
        assert len(rows) == 1
        assert rows[0]["state"] == "committed"
        assert rows[0]["replay_key"] == _replay_key_for(HITL_ID, TOOL_USE_ID), (
            "the reservation did not carry the replay key"
        )
        assert rows[0]["result_snapshot"] is not None, "no result was recorded to replay"

        # The replay: same hitl_id, same tool_use_id -> same replay key.
        second = await _invoke_replay(
            proxy, contract=contract, token=token, org=org, corr=corr, amount=3_000
        )

        assert calls == [3_000], "the handler ran again on a committed replay"
        assert second.output_json() == first.output_json(), (
            "the replay did not return the recorded result"
        )

        rows = await _reservation_rows(org)
        assert len(rows) == 1, "the replay placed a second reservation"
        env = await _envelope_row(org)
        assert env["spent_minor"] == 3_000, "the replay double-counted the spend"
        assert env["reserved_minor"] == 0
    finally:
        if proxy is not None:
            await proxy.drain_containment_tasks()
        await pool.close()
        await _drop_org(org)


@requires_app_role
async def test_a_held_replay_is_refused_and_places_no_second_hold() -> None:
    """`held`: in flight, outcome unknown. Refusing is the only safe answer."""
    import asyncpg

    from skylize.tools.base import ToolError, ToolSpendReplayInFlight

    calls: list[int] = []
    org = await _seed_envelope(ceiling_minor=100_000)
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=1, max_size=3)
    proxy = None
    try:
        ledger = SpendLedger(PostgresSpendRepository(pool))
        proxy, contract, token, corr, _ = await _spend_proxy(
            org=org, calls=calls, ledger=ledger,
        )

        replay_key = _replay_key_for(HITL_ID, TOOL_USE_ID)
        # A hold placed by the "first attempt" and never settled.
        held = await ledger.reserve(
            org_id=org, principal_id=PRINCIPAL, amount_minor=3_000,
            idempotency_key=f"tool:test.spend:{replay_key}",
            correlation_id=corr, replay_key=replay_key,
        )
        assert held.state == "held"

        with pytest.raises(ToolSpendReplayInFlight) as ei:
            await _invoke_replay(
                proxy, contract=contract, token=token, org=org, corr=corr,
                amount=3_000,
            )
        exc = ei.value
        assert isinstance(exc, ToolError), "a refused replay faulted the run"
        assert exc.retryable is True
        assert exc.failed_stage == "replay"

        assert calls == [], "the handler ran while the original was in flight"
        rows = await _reservation_rows(org)
        assert len(rows) == 1, "the replay placed a second hold"
        assert rows[0]["state"] == "held"
        env = await _envelope_row(org)
        assert env["reserved_minor"] == 3_000, "the replay double-held the budget"
        assert env["spent_minor"] == 0
    finally:
        if proxy is not None:
            await proxy.drain_containment_tasks()
        await pool.close()
        await _drop_org(org)


@requires_app_role
async def test_a_released_replay_spends_for_real() -> None:
    """`released`: the key is FREE. Under-counting here is worse than the double-count."""
    import asyncpg

    calls: list[int] = []
    org = await _seed_envelope(ceiling_minor=100_000)
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=1, max_size=3)
    proxy = None
    try:
        ledger = SpendLedger(PostgresSpendRepository(pool))
        proxy, contract, token, corr, _ = await _spend_proxy(
            org=org, calls=calls, ledger=ledger,
        )

        replay_key = _replay_key_for(HITL_ID, TOOL_USE_ID)
        first = await ledger.reserve(
            org_id=org, principal_id=PRINCIPAL, amount_minor=3_000,
            idempotency_key=f"tool:test.spend:{replay_key}",
            correlation_id=corr, replay_key=replay_key,
        )
        # The real release path, not a hand-written UPDATE.
        await ledger.release(org_id=org, reservation_id=first.reservation_id)

        # The replay must now place a REAL hold and run the tool for real.
        result = await _invoke_replay(
            proxy, contract=contract, token=token, org=org, corr=corr, amount=3_000
        )
        assert result.output_json()["ok"] is True
        assert calls == [3_000], "a released replay failed to spend for real"

        rows = await _reservation_rows(org)
        states = sorted(r["state"] for r in rows)
        assert states == ["committed", "released"], states
        env = await _envelope_row(org)
        assert env["spent_minor"] == 3_000, "the re-attempt did not count as spend"
        assert env["reserved_minor"] == 0
    finally:
        if proxy is not None:
            await proxy.drain_containment_tasks()
        await pool.close()
        await _drop_org(org)


@requires_app_role
async def test_an_expired_replay_spends_for_real() -> None:
    """`expired`: a worker died holding it. The re-attempt must still be able to spend.

    See `_expire_hold` for why the row is put into `expired` directly rather than
    through `sweep_expired`.
    """
    import asyncpg

    calls: list[int] = []
    org = await _seed_envelope(ceiling_minor=100_000)
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=1, max_size=3)
    proxy = None
    try:
        ledger = SpendLedger(PostgresSpendRepository(pool))
        proxy, contract, token, corr, _ = await _spend_proxy(
            org=org, calls=calls, ledger=ledger,
        )

        replay_key = _replay_key_for(HITL_ID, TOOL_USE_ID)
        first = await ledger.reserve(
            org_id=org, principal_id=PRINCIPAL, amount_minor=3_000,
            idempotency_key=f"tool:test.spend:{replay_key}",
            correlation_id=corr, replay_key=replay_key,
        )
        await _expire_hold(org, first.reservation_id)

        rows = await _reservation_rows(org)
        assert [r["state"] for r in rows] == ["expired"], rows

        result = await _invoke_replay(
            proxy, contract=contract, token=token, org=org, corr=corr, amount=3_000
        )
        assert result.output_json()["ok"] is True
        assert calls == [3_000], "an expired replay failed to spend for real"

        rows = await _reservation_rows(org)
        assert sorted(r["state"] for r in rows) == ["committed", "expired"]
        env = await _envelope_row(org)
        assert env["spent_minor"] == 3_000
        assert env["reserved_minor"] == 0
    finally:
        if proxy is not None:
            await proxy.drain_containment_tasks()
        await pool.close()
        await _drop_org(org)


@requires_app_role
async def test_a_committed_replay_without_a_recorded_result_refuses() -> None:
    """A committed row with no snapshot must refuse, never re-execute.

    Reachable for reservations settled before migration 0028 existed. Returning
    None and falling through would re-run a tool whose money already moved, so
    "we cannot produce the original result" gets its own type rather than being
    collapsed into "there was no original".
    """
    import asyncpg

    from skylize.tools.base import ToolError, ToolSpendReplayResultUnavailable

    calls: list[int] = []
    org = await _seed_envelope(ceiling_minor=100_000)
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=1, max_size=3)
    proxy = None
    try:
        ledger = SpendLedger(PostgresSpendRepository(pool))
        proxy, contract, token, corr, _ = await _spend_proxy(
            org=org, calls=calls, ledger=ledger,
        )

        replay_key = _replay_key_for(HITL_ID, TOOL_USE_ID)
        first = await ledger.reserve(
            org_id=org, principal_id=PRINCIPAL, amount_minor=3_000,
            idempotency_key=f"tool:test.spend:{replay_key}",
            correlation_id=corr, replay_key=replay_key,
        )
        # Committed WITHOUT a snapshot — a pre-0028 settlement.
        await ledger.commit(
            org_id=org, reservation_id=first.reservation_id, actual_minor=3_000,
        )

        with pytest.raises(ToolSpendReplayResultUnavailable) as ei:
            await _invoke_replay(
                proxy, contract=contract, token=token, org=org, corr=corr,
                amount=3_000,
            )
        exc = ei.value
        assert isinstance(exc, ToolError)
        assert exc.retryable is False, "waiting cannot fix a missing snapshot"

        assert calls == [], "the handler re-ran a spend that already committed"
        rows = await _reservation_rows(org)
        assert len(rows) == 1, "the replay placed a second reservation"
        env = await _envelope_row(org)
        assert env["spent_minor"] == 3_000, "the spend was counted twice"
    finally:
        if proxy is not None:
            await proxy.drain_containment_tasks()
        await pool.close()
        await _drop_org(org)


@requires_app_role
async def test_the_partial_index_blocks_a_second_live_row_but_frees_settled_ones() -> None:
    """The database rule itself, independent of the proxy that relies on it.

    Two `held` rows for one replay key must be impossible; once the first is
    released, a second must be allowed. This is what makes the
    `released`/`expired` behaviour above a property of the schema rather than of
    application code someone can forget to write.
    """
    import asyncpg

    org = await _seed_envelope(ceiling_minor=100_000)
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=1, max_size=3)
    try:
        ledger = SpendLedger(PostgresSpendRepository(pool))
        replay_key = _replay_key_for(HITL_ID, TOOL_USE_ID)

        first = await ledger.reserve(
            org_id=org, principal_id=PRINCIPAL, amount_minor=1_000,
            idempotency_key=f"tool:test.spend:{replay_key}:a",
            correlation_id=uuid.uuid4(), replay_key=replay_key,
        )

        # A DIFFERENT idempotency key, so only the partial index can stop this.
        with pytest.raises(asyncpg.UniqueViolationError):
            await ledger.reserve(
                org_id=org, principal_id=PRINCIPAL, amount_minor=1_000,
                idempotency_key=f"tool:test.spend:{replay_key}:b",
                correlation_id=uuid.uuid4(), replay_key=replay_key,
            )

        # Release the first, and the key is free again.
        await ledger.release(org_id=org, reservation_id=first.reservation_id)
        second = await ledger.reserve(
            org_id=org, principal_id=PRINCIPAL, amount_minor=1_000,
            idempotency_key=f"tool:test.spend:{replay_key}:c",
            correlation_id=uuid.uuid4(), replay_key=replay_key,
        )
        assert second.reservation_id != first.reservation_id
        assert second.state == "held"
    finally:
        await pool.close()
        await _drop_org(org)
