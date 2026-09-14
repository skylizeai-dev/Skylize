"""
Spend ledger — reserve / commit / release.

THE CORRECTION THIS MODULE ENCODES
----------------------------------
A budget ceiling cannot be enforced by a claim inside a signed token. A token is
a *copy*; a budget is a *shared mutable resource*. Two concurrent runs holding the
same "$500 ceiling" token will each read "under ceiling" and each spend $500.

So the token carries only:
    envelope_id      — WHICH budget this action draws from
    per_call_max     — a cheap local sanity bound

and the cumulative ceiling is enforced here, by a single conditional UPDATE whose
WHERE clause IS the policy. If the UPDATE affects zero rows, the action is denied.
There is no read-then-check window.

FAIL-CLOSED: a missing envelope, an expired period, a revoked envelope, and an
exhausted ceiling are all denials. Absence of budget is never unlimited budget.

LIFECYCLE
    reserve(key, amount)  -> hold
      ├── commit(actual)   -> hold released, actual moved into spent
      └── release()        -> hold released, nothing spent
      └── (crash)          -> swept at expires_at by `sweep_expired()`

Without the sweeper a crashed worker permanently consumes budget, and the failure
presents to the customer as "my agents stopped working and nobody knows why".

The `SpendRepository` port lives here; its asyncpg adapter, `PostgresSpendRepository`,
lives in `dal/spend_reservation.py` — `skylize.app` may not import `asyncpg`
directly (pyproject.toml's "Application logic contains no SQL (depends on dal
ports only)" import-linter contract), and that contract is checked transitively,
so the adapter cannot merely delegate its asyncpg-exception handling to a dal
helper while itself staying under `skylize.app.principal`; it has to actually
live in `skylize.dal`, matching every sibling ledger DAL (dal/cost_ledger.py,
dal/org_spend_ceiling.py).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from .errors import CeilingExceeded, EnvelopeNotFound
from .models import Reservation, SpendEnvelope

DEFAULT_HOLD_TTL = timedelta(minutes=15)


@runtime_checkable
class SpendRepository(Protocol):
    """Port. `PostgresSpendRepository` (dal/spend_reservation.py) is one
    adapter; tests use a fake.

    Every method takes `org_id` explicitly — there is no unscoped call, matching
    the Memory service convention in architecture/04_memory_architecture.md §5.
    """

    async def try_reserve(
        self,
        *,
        org_id: str,
        principal_id: str,
        amount_minor: int,
        idempotency_key: str,
        correlation_id: UUID,
        governance_token_id: UUID | None,
        now: datetime,
        expires_at: datetime,
        replay_key: UUID | None = None,
    ) -> Reservation | None:
        """Atomically place a hold. Returns None iff the ceiling would be breached.
        Raises `EnvelopeNotFound` if no active envelope exists."""

    async def commit(
        self,
        *,
        org_id: str,
        reservation_id: UUID,
        actual_minor: int,
        now: datetime,
        result_snapshot: dict[str, Any] | None = None,
    ) -> None: ...

    async def find_by_replay_key(
        self, *, org_id: str, replay_key: UUID
    ) -> Reservation | None:
        """The LIVE-or-SETTLED reservation for this replay key, or None.

        At most one can exist: migration 0028's partial unique index covers
        exactly `held` and `committed`. A `released` or `expired` predecessor is
        deliberately NOT returned -- those states free the key, and a replay that
        finds nothing here must place a real hold and spend for real."""

    async def release(
        self, *, org_id: str, reservation_id: UUID, now: datetime
    ) -> None: ...

    async def get_envelope(
        self, *, org_id: str, principal_id: str, now: datetime
    ) -> SpendEnvelope | None: ...

    async def sweep_expired(
        self, *, org_ids: Sequence[str], now: datetime, limit: int = 500
    ) -> int: ...


class SpendLedger:
    """Thin policy layer over the repository.

    Deliberately thin. The interesting concurrency guarantee lives in SQL, not
    here, because a guarantee expressed in Python across two round-trips is not a
    guarantee.
    """

    def __init__(self, repo: SpendRepository, *, hold_ttl: timedelta = DEFAULT_HOLD_TTL) -> None:
        self._repo = repo
        self._hold_ttl = hold_ttl

    async def reserve(
        self,
        *,
        org_id: str,
        principal_id: str,
        amount_minor: int,
        idempotency_key: str,
        correlation_id: UUID,
        governance_token_id: UUID | None = None,
        now: datetime | None = None,
        replay_key: UUID | None = None,
    ) -> Reservation:
        if amount_minor <= 0:
            raise ValueError("amount_minor must be > 0")
        now = now or datetime.now(timezone.utc)

        held = await self._repo.try_reserve(
            org_id=org_id,
            principal_id=principal_id,
            amount_minor=amount_minor,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            governance_token_id=governance_token_id,
            now=now,
            expires_at=now + self._hold_ttl,
            replay_key=replay_key,
        )
        if held is not None:
            return held

        # The UPDATE matched nothing. Re-read ONLY to produce a good reason —
        # never to make the decision. The decision was already made, atomically.
        envelope = await self._repo.get_envelope(
            org_id=org_id, principal_id=principal_id, now=now
        )
        if envelope is None:
            raise EnvelopeNotFound(
                f"no active spend envelope for principal={principal_id!r} "
                f"org={org_id!r} at {now.isoformat()}"
            )
        raise CeilingExceeded(
            f"reservation of {amount_minor} would exceed envelope "
            f"{envelope.envelope_id}: available={envelope.available_minor} "
            f"{envelope.currency}",
            defer_to_human=envelope.over_ceiling_behavior == "defer_to_human",
        )

    async def commit(
        self,
        *,
        org_id: str,
        reservation_id: UUID,
        actual_minor: int,
        now: datetime | None = None,
        result_snapshot: dict[str, Any] | None = None,
    ) -> None:
        """Settle a hold with the real cost.

        `actual_minor` may be lower than the hold (normal — LLM cost is known only
        after the call). It may NOT be higher: over-spend is a policy event, not a
        rounding detail, and must go back through `reserve` for the delta.
        """
        if actual_minor < 0:
            raise ValueError("actual_minor must be >= 0")
        await self._repo.commit(
            org_id=org_id,
            reservation_id=reservation_id,
            actual_minor=actual_minor,
            now=now or datetime.now(timezone.utc),
            result_snapshot=result_snapshot,
        )

    async def find_replay(self, *, org_id: str, replay_key: UUID) -> Reservation | None:
        """The reservation already standing for this replay key, if any.

        Returns a row only in `held` or `committed` -- the two states that must
        block a replay (docs/architecture/spend_reservation_replay_semantics.md
        section 6). `released` and `expired` free the key, so they read as None
        here and the caller proceeds to a real reservation.
        """
        return await self._repo.find_by_replay_key(org_id=org_id, replay_key=replay_key)

    async def release(
        self, *, org_id: str, reservation_id: UUID, now: datetime | None = None
    ) -> None:
        await self._repo.release(
            org_id=org_id,
            reservation_id=reservation_id,
            now=now or datetime.now(timezone.utc),
        )
