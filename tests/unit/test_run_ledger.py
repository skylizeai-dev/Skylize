"""
RunLedger unit tests (in-memory backend).

Proves the two fail-closed ceilings: cumulative debits never overrun the budget
(and an over-debit is not committed), and a run past its time ceiling refuses
further debits. Time is driven by an injected clock so the TTL test is
deterministic — no sleeps.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from skylize.runtime import InMemoryRunLedger, RunExpired, TokenBudgetExceeded


async def test_debit_accumulates_and_returns_remaining() -> None:
    ledger = InMemoryRunLedger()
    cid = uuid4()
    await ledger.open_run(cid, "agent_a", budget=1000, ttl_seconds=60)

    assert await ledger.debit(cid, "agent_a", 300) == 700
    assert await ledger.debit(cid, "agent_a", 250) == 450
    assert await ledger.used(cid, "agent_a") == 550


async def test_overdraft_raises_and_is_not_committed() -> None:
    ledger = InMemoryRunLedger()
    cid = uuid4()
    await ledger.open_run(cid, "agent_a", budget=1000, ttl_seconds=60)
    await ledger.debit(cid, "agent_a", 600)

    with pytest.raises(TokenBudgetExceeded):
        await ledger.debit(cid, "agent_a", 500)  # 1100 > 1000

    # The rejected debit left usage untouched, so the remaining budget is intact.
    assert await ledger.used(cid, "agent_a") == 600
    assert await ledger.debit(cid, "agent_a", 400) == 0


async def test_ttl_expiry_fails_closed() -> None:
    clock = {"now": 0.0}
    ledger = InMemoryRunLedger(clock=lambda: clock["now"])
    cid = uuid4()
    await ledger.open_run(cid, "agent_a", budget=1000, ttl_seconds=10)

    assert await ledger.debit(cid, "agent_a", 100) == 900  # inside the window

    clock["now"] = 10.5  # past the deadline (0 + 10)
    with pytest.raises(RunExpired):
        await ledger.debit(cid, "agent_a", 1)


async def test_debit_on_unopened_run_fails_closed() -> None:
    ledger = InMemoryRunLedger()
    with pytest.raises(RunExpired):
        await ledger.debit(uuid4(), "ghost_agent", 1)


async def test_open_run_is_idempotent_for_a_live_run() -> None:
    ledger = InMemoryRunLedger()
    cid = uuid4()
    await ledger.open_run(cid, "agent_a", budget=1000, ttl_seconds=60)
    await ledger.debit(cid, "agent_a", 400)

    # Re-opening a live run must not reset its budget or usage.
    await ledger.open_run(cid, "agent_a", budget=1000, ttl_seconds=60)
    assert await ledger.used(cid, "agent_a") == 400


async def test_open_run_on_expired_run_fails_closed() -> None:
    clock = {"now": 0.0}
    ledger = InMemoryRunLedger(clock=lambda: clock["now"])
    cid = uuid4()
    await ledger.open_run(cid, "agent_a", budget=1000, ttl_seconds=10)

    clock["now"] = 11.0
    with pytest.raises(RunExpired):
        await ledger.open_run(cid, "agent_a", budget=1000, ttl_seconds=10)
