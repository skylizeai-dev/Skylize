"""OD30 vs `sweep_expired`, on REAL Postgres, as the app role. Strict xfail.

The claim (owner ruling OD30, docs/decisions/2026-11-07_owner_rulings.md): a spend
hold KEPT because a tool's outcome is unknown must not be released by the passage
of time; only reconciliation evidence may release it
(docs/architecture/idempotency_reconciliation_design.md).

What is real here, with nothing standing in for it:
  * the hold is placed and retained by the real `ToolProxy.invoke` path
    (tools/proxy.py, the `except Exception` branch that keeps the reservation when
    the handler raises `ToolOutcomeUnknownError`);
  * the row sits in real Postgres under FORCE RLS, written through the
    non-superuser `skylize_app` role;
  * `PostgresSpendRepository.sweep_expired` is the real method, run against that
    row with the clock moved past DEFAULT_HOLD_TTL.
The only synthetic input is the handler raising the exception, which is the
trigger, not the behavior under test. No SQL semantics are imitated.

It is expected to FAIL today: the sweeper selects on `state = 'held' AND
expires_at < now` alone, so the retained hold is expired and its amount is handed
back to the envelope (dal/spend_reservation.py, `sweep_expired`). Strict, so the
moment the sweeper is fixed this test XPASSes, the run goes red, and whoever fixed
it must delete the marker. `raises=AssertionError` pins WHAT may fail: setup
problems below raise RuntimeError and so surface as real failures instead of being
swallowed as "expected".

Skipped unless SKYLIZE_TEST_DB_URL and SKYLIZE_TEST_APP_DB_URL are both set.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from skylize.app.principal.spend import DEFAULT_HOLD_TTL, SpendLedger
from skylize.dal.spend_reservation import PostgresSpendRepository

from .conftest import APP_DB_URL, requires_app_role
from .test_tool_proxy_reservation_conflict_pg import _spend_proxy
from .test_tool_proxy_spend_pg import (
    _drop_org,
    _envelope_row,
    _reservation_states,
    _seed_envelope,
)

pytestmark = pytest.mark.integration

HOLD_MINOR = 1_000


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "OD30 vs sweep_expired: held reservations expire at 15 min; fix needs "
        "reconcile_required_at marker + sweeper predicate migration"
    ),
)
@requires_app_role
async def test_a_hold_kept_by_od30_is_not_released_by_sweep_expired() -> None:
    import asyncpg

    from skylize.tools.base import ToolOutcomeUnknownError

    org = await _seed_envelope(ceiling_minor=100_000)
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=1, max_size=3)
    try:
        repo = PostgresSpendRepository(pool)
        calls: list[int] = []
        proxy, contract, token, corr, _containment = await _spend_proxy(
            org=org, ledger=SpendLedger(repo), calls=calls
        )

        async def outcome_unknown(_inp, _ctx):  # type: ignore[no-untyped-def]
            raise ToolOutcomeUnknownError("simulated ambiguous 5xx after the request left")

        # Swap ONLY the handler of the registered tool; every other layer is the
        # helper's real wiring. `ToolDefinition` is frozen, hence model_copy, and
        # the registry has no replace API, hence its cache.
        registry = proxy.registry
        registry._cache["test.spend"] = registry.resolve("test.spend").model_copy(
            update={"handler": outcome_unknown}
        )

        with pytest.raises(ToolOutcomeUnknownError):
            await proxy.invoke(
                tool_id="test.spend", input_data={"amount_minor": HOLD_MINOR},
                governance_token=token, contract=contract, org_id=org,
                correlation_id=corr,
            )

        # PRECONDITIONS. RuntimeError, not assert: a broken setup must not be
        # absorbed by the xfail above.
        states = await _reservation_states(org)
        if states != {"held": 1}:
            raise RuntimeError(f"OD30 did not retain the hold; states={states}")
        if (await _envelope_row(org))["reserved_minor"] != HOLD_MINOR:
            raise RuntimeError("retained hold is not reflected in reserved_minor")

        # Let the clock pass the hold's expiry and run the real sweeper.
        later = datetime.now(timezone.utc) + DEFAULT_HOLD_TTL * 2
        reclaimed = await repo.sweep_expired(org_ids=[org], now=later)

        # THE CLAIM. Fails today: reclaimed == 1, state == 'expired', reserved == 0.
        assert await _reservation_states(org) == {"held": 1}, (
            f"sweep_expired released an OD30-retained hold (reclaimed={reclaimed})"
        )
        assert (await _envelope_row(org))["reserved_minor"] == HOLD_MINOR, (
            "sweep_expired handed the retained hold's amount back to the ceiling"
        )
        assert reclaimed == 0
    finally:
        await pool.close()
        await _drop_org(org)
