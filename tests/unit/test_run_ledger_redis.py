"""
Unit tests for RedisRunLedger — mock Redis, no real infra required.

Covers open_run, used, debit (ok/expired/budget-exceeded) via a MagicMock
Redis client so the Lua script path is exercised through the mock.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from skylize.runtime import RunExpired, TokenBudgetExceeded


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_redis_mock(
    *,
    hsetnx_return: int = 1,
    debit_return: tuple[int, int] = (0, 900),
    hget_return: str | None = "100",
) -> MagicMock:
    """Return a mock redis.asyncio.Redis client."""
    client = MagicMock()
    client.hsetnx = AsyncMock(return_value=hsetnx_return)
    client.hset = AsyncMock(return_value=1)
    client.expire = AsyncMock(return_value=True)
    client.hget = AsyncMock(return_value=hget_return)

    # register_script returns a callable mock
    script_mock = AsyncMock(return_value=debit_return)
    client.register_script = MagicMock(return_value=script_mock)
    client._debit_script = script_mock  # store for assertion access
    return client


def _make_ledger(client: MagicMock):
    """Construct RedisRunLedger with an injected mock client."""
    from skylize.runtime.run_ledger import RedisRunLedger

    with patch("redis.asyncio.from_url", return_value=client):
        ledger = RedisRunLedger("redis://localhost:6379")
    # The Lua script mock registered at construction time:
    ledger._debit = client._debit_script
    return ledger


# ---------------------------------------------------------------------------
# open_run
# ---------------------------------------------------------------------------

async def test_redis_open_run_first_time_sets_budget_and_ttl() -> None:
    client = _make_redis_mock(hsetnx_return=1)
    ledger = _make_ledger(client)
    cid = uuid4()

    await ledger.open_run(cid, "agent_a", budget=1000, ttl_seconds=60)

    client.hsetnx.assert_called_once()
    client.hset.assert_called_once()
    client.expire.assert_called_once()


async def test_redis_open_run_idempotent_when_key_exists() -> None:
    # hsetnx returns 0 → key already existed, do nothing extra
    client = _make_redis_mock(hsetnx_return=0)
    ledger = _make_ledger(client)
    cid = uuid4()

    await ledger.open_run(cid, "agent_a", budget=1000, ttl_seconds=60)

    client.hsetnx.assert_called_once()
    client.hset.assert_not_called()
    client.expire.assert_not_called()


# ---------------------------------------------------------------------------
# used
# ---------------------------------------------------------------------------

async def test_redis_used_returns_parsed_integer() -> None:
    client = _make_redis_mock(hget_return="350")
    ledger = _make_ledger(client)

    result = await ledger.used(uuid4(), "agent_a")
    assert result == 350


async def test_redis_used_returns_zero_when_key_missing() -> None:
    client = _make_redis_mock(hget_return=None)
    ledger = _make_ledger(client)

    result = await ledger.used(uuid4(), "agent_a")
    assert result == 0


# ---------------------------------------------------------------------------
# debit — success
# ---------------------------------------------------------------------------

async def test_redis_debit_success_returns_remaining() -> None:
    client = _make_redis_mock(debit_return=(0, 700))
    ledger = _make_ledger(client)

    remaining = await ledger.debit(uuid4(), "agent_a", 300)
    assert remaining == 700


# ---------------------------------------------------------------------------
# debit — run expired (-1)
# ---------------------------------------------------------------------------

async def test_redis_debit_expired_run_raises_run_expired() -> None:
    client = _make_redis_mock(debit_return=(-1, 0))
    ledger = _make_ledger(client)

    with pytest.raises(RunExpired):
        await ledger.debit(uuid4(), "agent_a", 100)


# ---------------------------------------------------------------------------
# debit — budget exceeded (-2)
# ---------------------------------------------------------------------------

async def test_redis_debit_budget_exceeded_raises() -> None:
    client = _make_redis_mock(debit_return=(-2, -50))
    ledger = _make_ledger(client)

    with pytest.raises(TokenBudgetExceeded):
        await ledger.debit(uuid4(), "agent_a", 500)


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------

async def test_redis_close_calls_aclose() -> None:
    client = _make_redis_mock()
    client.aclose = AsyncMock()
    ledger = _make_ledger(client)

    await ledger.close()
    client.aclose.assert_called_once()
