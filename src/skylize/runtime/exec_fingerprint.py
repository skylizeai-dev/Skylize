"""
Tool-execution fingerprinting and pre-dispatch dedup (IF-TOOL support).

This module is the **dedup** building block for the tool proxy described in
[agent_runtime.md §5](../../docs/architecture/03_agent_runtime.md#5-tool-proxy-if-tool).
The proxy itself is not yet authored; when it is, the dedup step slots into its
validation chain *after* the canonical governance order and *before* dispatch:

    signature -> expiry -> revocation -> scope -> budget -> delegation -> DEDUP -> dispatch

Dedup is deliberately **not** a governance validation stage (those end at
``delegation`` per agent_governance.md §4.3). It is a side-effect-suppression
cache layer: two identical, concurrently-issued tool calls within the dedup
window collapse to a single dispatch, and the second caller is served the first
call's result instead of re-executing it.

Fingerprint
-----------
``SHA-256(canonical_json({org_id, tool_name, normalized_args}))`` — the same
canonical-JSON discipline the audit trail uses (``sort_keys``, tight separators,
``default=str``), so two semantically-identical calls hash identically
regardless of dict key ordering. ``org_id`` is part of the key, so dedup never
crosses a tenant boundary.

Cache contract
--------------
``DedupCache`` is a port. The production implementation is Redis
(``SETNX toolexec:{org_id}:{fingerprint}`` with a short TTL), wired in a concrete
adapter at the composition root — NOT here: this module is a pure inner layer and
holds no database/cache driver (import-linter "Pure inner layers hold no database
driver"). ``InMemoryDedupCache`` is the single-process implementation used by
tests and the ``memory`` backend; it reproduces the SETNX-with-TTL semantics the
Redis key relies on.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

# Default time-to-live for a dedup reservation, in seconds. Matches the Redis
# `SETNX toolexec:{org_id}:{fingerprint}` TTL: long enough to collapse a burst of
# duplicate concurrent calls, short enough that a later legitimate re-issue of the
# same call is not suppressed.
DEFAULT_DEDUP_TTL_SECONDS = 60

# Redis key namespace for a dedup reservation (documented here so the future
# Redis-backed cache and this in-memory one agree on the shape).
_KEY_PREFIX = "toolexec"


def normalize_args(args: Any) -> Any:
    """Return a canonical, order-independent form of tool arguments.

    Dict keys are sorted recursively so ``{"a": 1, "b": 2}`` and
    ``{"b": 2, "a": 1}`` produce the same fingerprint. Lists keep their order
    (argument order is semantically meaningful), but their elements are
    normalized. Scalars pass through unchanged. The result is fed to
    ``json.dumps(..., sort_keys=True)``, so this normalization is belt-and-braces
    for nested structures and for making the intent explicit.
    """
    if isinstance(args, dict):
        return {key: normalize_args(args[key]) for key in sorted(args)}
    if isinstance(args, (list, tuple)):
        return [normalize_args(item) for item in args]
    return args


def canonical_args_json(args: Any) -> str:
    """Deterministic JSON serialization of normalized tool arguments."""
    return json.dumps(
        normalize_args(args),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def compute_exec_fingerprint(*, org_id: str, tool_name: str, args: Any) -> str:
    """SHA-256 hex fingerprint of a tool execution.

    ``SHA-256(canonical_json({org_id, tool_name, normalized_args}))``. Stable
    across process restarts and across hosts: identical (org, tool, args) always
    yields the same hex digest, which is what makes cross-process dedup via a
    shared Redis key correct.
    """
    body = json.dumps(
        {
            "org_id": org_id,
            "tool_name": tool_name,
            "normalized_args": normalize_args(args),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def dedup_key(org_id: str, fingerprint: str) -> str:
    """The cache key for a dedup reservation: ``toolexec:{org_id}:{fingerprint}``."""
    return f"{_KEY_PREFIX}:{org_id}:{fingerprint}"


@dataclass(frozen=True, slots=True)
class DedupOutcome:
    """Result of attempting a dedup reservation.

    - ``reserved=True``  -> this caller WON the race (cache miss); it must
      dispatch the tool and then call ``store`` with the result.
    - ``reserved=False`` -> a concurrent/earlier caller already holds the key
      (cache hit); ``cached_result`` carries that call's result if it has been
      stored yet (it may be ``None`` if the winner is still in flight).
    """

    reserved: bool
    cached_result: str | None = None


class DedupCache(Protocol):
    """Port for the pre-dispatch dedup cache.

    Production binding: Redis ``SETNX``/``GET``/``SETEX`` over
    ``toolexec:{org_id}:{fingerprint}``. Pure inner layers depend on this port,
    never on a cache driver.
    """

    async def try_reserve(self, key: str, *, ttl_seconds: int) -> DedupOutcome:
        """Atomically claim ``key`` (SETNX).

        Returns ``DedupOutcome(reserved=True)`` on a fresh claim (the caller
        dispatches), or ``DedupOutcome(reserved=False, cached_result=...)`` if the
        key already exists (the caller is deduped and served the cached result).
        """
        ...

    async def store(self, key: str, result: str, *, ttl_seconds: int) -> None:
        """Persist the winning call's result under ``key`` for the TTL window."""
        ...

    async def get(self, key: str) -> str | None:
        """Return the stored result for ``key``, or ``None`` if absent/expired."""
        ...


class InMemoryDedupCache:
    """Single-process ``DedupCache`` reproducing Redis SETNX-with-TTL semantics.

    Used by tests and the ``memory`` backend. A monotonic logical clock drives
    TTL so expiry is deterministic and the module imports no wall-clock source
    (``time``/``datetime``), keeping it replay-safe and trivially testable. Each
    ``try_reserve``/``store``/``get`` advances the clock by one tick; pass an
    explicit ``ttl_seconds`` of ticks to control the window in tests.

    Concurrency note: the async methods contain no ``await`` points between the
    read and the write, so under cooperative scheduling (the asyncio event loop)
    a reservation is effectively atomic — exactly one of N concurrent
    ``try_reserve`` calls for the same key wins, matching Redis ``SETNX``.
    """

    def __init__(self) -> None:
        # key -> (result_or_None, expires_at_tick)
        self._store: dict[str, tuple[str | None, int]] = {}
        self._clock = 0

    def _tick(self) -> int:
        self._clock += 1
        return self._clock

    def _live(self, key: str, now: int) -> tuple[str | None, int] | None:
        """Return a non-expired entry for ``key``, evicting it if it has expired."""
        entry = self._store.get(key)
        if entry is None:
            return None
        if entry[1] <= now:
            del self._store[key]
            return None
        return entry

    async def try_reserve(
        self, key: str, *, ttl_seconds: int = DEFAULT_DEDUP_TTL_SECONDS
    ) -> DedupOutcome:
        now = self._tick()
        entry = self._live(key, now)
        if entry is not None:
            # Key already held — this caller is deduped, served whatever result
            # the winner has stored so far (None while the winner is in flight).
            return DedupOutcome(reserved=False, cached_result=entry[0])
        # Fresh claim: reserve with no result yet; winner fills it via `store`.
        self._store[key] = (None, now + ttl_seconds)
        return DedupOutcome(reserved=True)

    async def store(
        self, key: str, result: str, *, ttl_seconds: int = DEFAULT_DEDUP_TTL_SECONDS
    ) -> None:
        now = self._tick()
        self._store[key] = (result, now + ttl_seconds)

    async def get(self, key: str) -> str | None:
        now = self._tick()
        entry = self._live(key, now)
        return None if entry is None else entry[0]
