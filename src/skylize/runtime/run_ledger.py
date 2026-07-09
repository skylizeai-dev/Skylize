"""
The RunLedger — per-run token accounting for the tool proxy.

A *run* is one orchestrated agent invocation. Exactly one governance token is
minted per run, so the token id is the natural run key. The ledger tracks
``tokens_used_so_far`` for a run and enforces two ceilings, fail-closed:

  - **budget**: cumulative usage may never exceed ``max_token_budget`` — an
    over-debit raises ``TokenBudgetExceeded`` and is NOT committed;
  - **time**: a run's ledger entry lives at most ``max_execution_time_seconds``;
    operating on an expired run raises ``RunExpired``.

Two interchangeable backends sit behind the ``RunLedger`` port:
  - ``InMemoryRunLedger`` — a dict, used by the ``memory`` backend and tests;
    expiry is driven by an injectable monotonic ``clock`` so it is deterministic.
  - ``RedisRunLedger`` — a Redis hash per run with an atomic Lua debit, for the
    multi-instance ``postgres`` backend.

This module is a pure inner layer: it imports no database driver (asyncpg is
forbidden by the import-linter contract). Redis is loaded lazily, only when the
Redis backend is actually constructed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable
from uuid import UUID

from ..adapters.llm.gateway import TokenBudgetExceeded

__all__ = [
    "RunLedger",
    "InMemoryRunLedger",
    "RedisRunLedger",
    "RunExpired",
    "TokenBudgetExceeded",
]


class RunExpired(Exception):
    """Raised when a run's ledger entry is unknown or past its time ceiling."""


def _key(correlation_id: UUID, agent_id: str) -> tuple[str, str]:
    return (str(correlation_id), agent_id)


@runtime_checkable
class RunLedger(Protocol):
    """Per-run token accounting port. Implementations MUST fail closed."""

    async def open_run(
        self, correlation_id: UUID, agent_id: str, *, budget: int, ttl_seconds: int
    ) -> None:
        """Seed a run's budget + time ceiling. Idempotent for a live run; raises
        ``RunExpired`` if the run already exists and its ceiling has passed."""
        ...

    async def used(self, correlation_id: UUID, agent_id: str) -> int:
        """Tokens debited so far for the run (0 if the run is unknown)."""
        ...

    async def debit(self, correlation_id: UUID, agent_id: str, tokens: int) -> int:
        """Debit ``tokens`` and return the remaining budget.

        Raises ``TokenBudgetExceeded`` if the debit would overrun the budget (the
        debit is NOT applied) and ``RunExpired`` if the run is gone/expired.
        """
        ...


# ---------------------------------------------------------------------------
# In-memory backend (memory backend + tests)
# ---------------------------------------------------------------------------

@dataclass
class _Entry:
    budget: int
    used: int
    deadline: float  # monotonic-clock instant after which the run is expired


class InMemoryRunLedger:
    """Single-process ledger. Expiry is driven by an injectable ``clock`` so
    tests can advance time deterministically with no sleeps."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._runs: dict[tuple[str, str], _Entry] = {}

    async def open_run(
        self, correlation_id: UUID, agent_id: str, *, budget: int, ttl_seconds: int
    ) -> None:
        key = _key(correlation_id, agent_id)
        entry = self._runs.get(key)
        if entry is None:
            self._runs[key] = _Entry(
                budget=budget, used=0, deadline=self._clock() + ttl_seconds
            )
            return
        if self._clock() > entry.deadline:
            raise RunExpired(f"run {key} expired before this call")
        # Live run already open — leave its budget/deadline untouched.

    async def used(self, correlation_id: UUID, agent_id: str) -> int:
        entry = self._runs.get(_key(correlation_id, agent_id))
        return entry.used if entry is not None else 0

    async def debit(self, correlation_id: UUID, agent_id: str, tokens: int) -> int:
        key = _key(correlation_id, agent_id)
        entry = self._runs.get(key)
        if entry is None:
            raise RunExpired(f"run {key} is not open")
        if self._clock() > entry.deadline:
            raise RunExpired(f"run {key} expired")
        new_used = entry.used + tokens
        remaining = entry.budget - new_used
        if remaining < 0:
            raise TokenBudgetExceeded(
                f"debit of {tokens} would overrun budget={entry.budget} "
                f"(used={entry.used})"
            )
        entry.used = new_used
        return remaining


# ---------------------------------------------------------------------------
# Redis backend (postgres / multi-instance)
# ---------------------------------------------------------------------------

# Atomic debit: read used+budget from the run hash, refuse the over-debit, else
# commit. KEYS[1]=hash, ARGV[1]=tokens. Returns {status, remaining} where status
# is 0 (ok), -1 (unknown/expired run), or -2 (budget exceeded).
_DEBIT_LUA = """
local used = redis.call('HGET', KEYS[1], 'used')
if used == false then return {-1, 0} end
local budget = tonumber(redis.call('HGET', KEYS[1], 'budget'))
local newv = tonumber(used) + tonumber(ARGV[1])
local remaining = budget - newv
if remaining < 0 then return {-2, remaining} end
redis.call('HSET', KEYS[1], 'used', newv)
return {0, remaining}
"""


class RedisRunLedger:
    """Redis-backed ledger. One hash per run (`runledger:{token}:{agent}`) with
    fields ``used`` + ``budget``; native key TTL bounds the run's lifetime."""

    def __init__(self, url: str) -> None:
        import redis.asyncio as redis  # lazy: only the Redis backend needs it

        self._client: Any = redis.from_url(url, decode_responses=True)
        self._debit = self._client.register_script(_DEBIT_LUA)

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _redis_key(correlation_id: UUID, agent_id: str) -> str:
        return f"runledger:{correlation_id}:{agent_id}"

    async def open_run(
        self, correlation_id: UUID, agent_id: str, *, budget: int, ttl_seconds: int
    ) -> None:
        key = self._redis_key(correlation_id, agent_id)
        # HSETNX returns 1 only when the field is freshly created → first open.
        created = await self._client.hsetnx(key, "budget", budget)
        if created:
            await self._client.hset(key, "used", 0)
            await self._client.expire(key, ttl_seconds)

    async def used(self, correlation_id: UUID, agent_id: str) -> int:
        raw = await self._client.hget(self._redis_key(correlation_id, agent_id), "used")
        return int(raw) if raw is not None else 0

    async def debit(self, correlation_id: UUID, agent_id: str, tokens: int) -> int:
        key = self._redis_key(correlation_id, agent_id)
        status, remaining = await self._debit(keys=[key], args=[tokens])
        if status == -1:
            raise RunExpired(f"run {key} is gone (expired or never opened)")
        if status == -2:
            raise TokenBudgetExceeded(
                f"debit of {tokens} would overrun the run budget ({remaining} short)"
            )
        return int(remaining)
