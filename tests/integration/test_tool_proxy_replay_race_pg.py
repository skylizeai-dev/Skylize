"""The replay-guard TOCTOU race — REAL Postgres, driven through `ToolProxy.invoke`.

`_replay_guard` reads (`find_replay`) then, if it saw nothing, the caller reserves
(`_reserve_spend` -> `try_reserve`) as two SEPARATE transactions. Between them sits
a window `_replay_guard` cannot close on its own: two concurrent `invoke()` calls
carrying the SAME replay key (duplicate delivery of one HITL approval, e.g. a
retried webhook) can both see `find_replay` return None -- neither's INSERT has
committed yet -- and both proceed to place a hold.

Migration 0029's partial unique index (`spend_reservation_replay_live`, on
`(org_id, replay_key) WHERE replay_key IS NOT NULL AND state IN ('held',
'committed')`) still enforces "at most one live row per replay key", so the LOSER
of that race gets a Postgres unique-violation on its INSERT.
`PostgresSpendRepository.try_reserve` (app/principal/spend.py) catches that
`asyncpg.UniqueViolationError` at the repository boundary and raises the domain
exception `ReplayKeyConflict` (app/principal/errors.py) instead -- the same
boundary discipline `CeilingExceeded` and `EnvelopeNotFound` already keep, so no
database-specific detail leaks past the repository.

Before this fix `ReplayKeyConflict` was raised by the ledger and caught nowhere
above it: it would have escaped `ToolProxy.invoke` as a bare `BudgetError`, missed
`except ToolError` (app/agents/execution.py:981), and faulted the whole agent run
over a race the caller did nothing wrong to trigger.

The fix (tools/proxy.py `_reserve_spend`) adds an `except ReplayKeyConflict`
clause and maps it onto the SAME `ToolSpendReplayInFlight` `_replay_guard` already
raises when it detects a `held` row synchronously (see
test_tool_proxy_replay_states_pg.py) -- semantically the identical fact, just
detected by the database instead of by the read.

The race is forced deterministically rather than hoped for: `_BarrierLedger` makes
both concurrent calls' `find_replay` return only after BOTH have entered it, so
neither can have committed a reservation yet when the other reads. Without the
barrier this test would still usually reproduce the race under real Postgres I/O,
but "usually" is not a regression test.

Everything runs as the non-superuser, non-table-owner `skylize_app` role, so RLS is
genuinely in force. Skipped unless SKYLIZE_TEST_DB_URL and SKYLIZE_TEST_APP_DB_URL
are both set.
"""

from __future__ import annotations

import asyncio

import pytest

from skylize.app.principal.spend import PostgresSpendRepository, SpendLedger
from skylize.tools.base import ToolError, ToolSpendReplayInFlight

from .conftest import APP_DB_URL, requires_app_role
from .test_tool_proxy_reservation_conflict_pg import _spend_proxy
from .test_tool_proxy_replay_states_pg import _invoke_replay, _reservation_rows
from .test_tool_proxy_spend_pg import _drop_org, _envelope_row, _seed_envelope

pytestmark = pytest.mark.integration


class _BarrierLedger(SpendLedger):
    """Delays `find_replay` until both racing callers have entered it.

    `_replay_guard` calls `find_replay` first and only reserves if it returns
    None. Releasing both callers together, after both have read, guarantees
    each sees None -- exactly the TOCTOU window a real duplicate delivery can
    hit by accident, made deterministic instead of merely likely.
    """

    def __init__(self, repo: PostgresSpendRepository, *, gate: asyncio.Barrier) -> None:
        super().__init__(repo)
        self._gate = gate

    async def find_replay(self, **kwargs: object):  # type: ignore[override]
        result = await super().find_replay(**kwargs)
        await self._gate.wait()
        return result


@requires_app_role
async def test_a_replay_race_surfaces_as_replay_in_flight_not_a_raw_db_error() -> None:
    """The loser of the race must get a handled `ToolError`, and the run must not fault.

    Drives the race through `ToolProxy.invoke` end-to-end -- not `try_reserve` at
    the repository layer, which already proves the database enforces uniqueness
    (test_tool_proxy_replay_states_pg.py::test_the_partial_index_blocks_a_second_
    live_row_but_frees_settled_ones) but says nothing about what a caller sitting
    above `invoke` receives.
    """
    import asyncpg

    calls: list[int] = []
    org = await _seed_envelope(ceiling_minor=100_000)
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=2, max_size=4)
    proxy = None
    try:
        gate = asyncio.Barrier(2)
        ledger = _BarrierLedger(PostgresSpendRepository(pool), gate=gate)
        proxy, contract, token, corr, containment = await _spend_proxy(
            org=org, calls=calls, ledger=ledger,
        )

        results = await asyncio.gather(
            _invoke_replay(
                proxy, contract=contract, token=token, org=org, corr=corr,
                amount=3_000,
            ),
            _invoke_replay(
                proxy, contract=contract, token=token, org=org, corr=corr,
                amount=3_000,
            ),
            return_exceptions=True,
        )

        successes = [r for r in results if not isinstance(r, BaseException)]
        failures = [r for r in results if isinstance(r, BaseException)]

        assert len(successes) == 1, (
            "exactly one concurrent replay attempt must win the race and run"
        )
        assert len(failures) == 1, (
            "exactly one concurrent replay attempt must lose the race and be refused"
        )

        loser = failures[0]
        # THE fix: without proxy.py's `except ReplayKeyConflict` clause, the
        # ledger's `ReplayKeyConflict` would fail this assertion immediately --
        # it would escape `except ToolError` in app/agents/execution.py:981 and
        # fault the whole agent run.
        assert isinstance(loser, ToolError), (
            f"the losing replay surfaced as {type(loser).__name__}, not a "
            f"handled ToolError -- it would have faulted the agent run"
        )
        assert isinstance(loser, ToolSpendReplayInFlight), (
            "a replay-race collision must map to the SAME type a sequential "
            "in-flight replay gets, so a caller branching on the type does not "
            "need to know which of the two paths produced it"
        )
        assert loser.retryable is True
        assert loser.failed_stage == "replay"

        # The handler ran exactly once -- the race must not double-execute the
        # side effect it exists to prevent a duplicate of.
        assert calls == [3_000], calls

        rows = await _reservation_rows(org)
        assert len(rows) == 1, "the loser's collided INSERT must not leave a row behind"
        assert rows[0]["state"] == "committed"

        env = await _envelope_row(org)
        assert env["spent_minor"] == 3_000, "the winner's spend must be recorded exactly once"
        assert env["reserved_minor"] == 0, "no hold may be left outstanding after the race"

        await proxy.drain_containment_tasks()
        assert containment.proposals == [], (
            "a replay race is not a customer overspending; the containment "
            "auto-hook must not fire for it"
        )
    finally:
        if proxy is not None:
            await proxy.drain_containment_tasks()
        await pool.close()
        await _drop_org(org)
