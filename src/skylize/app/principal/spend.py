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

PROMPT 0 AUDIT NOTE (driver + RLS GUC): confirmed asyncpg is the runtime driver
(pyproject.toml — asyncpg>=0.29; SQLAlchemy is Alembic-only) and the RLS GUC used
by every existing policy is `skylize.org_id`, not `app.org_id` (e.g.
migrations/versions/0007_org_credentials.py, dal/connection.py:79) — fixed below.

CONNECTION-PATTERN NOTE (not fixed here, flagged for the wiring pass):
`dal/connection.py` documents itself as "the ONLY module that opens an asyncpg
connection," and every existing DAL class takes a `Database` and runs its
tenant-scoped queries inside `Database.tenant_session(org_id)` rather than
acquiring a pool and calling `set_config` itself (see dal/cost_ledger.py,
dal/org_spend_ceiling.py). `PostgresSpendRepository` below still does the latter,
which is why it can stay under `skylize.app.principal` at all: importing
`skylize.dal.connection.Database` from here would trip the enforced
import-linter contract "Application logic contains no SQL (depends on dal ports
only)" (pyproject.toml — forbids skylize.app -> skylize.dal.connection). Proper
placement is `skylize.dal`, matching every sibling ledger DAL — deferred to the
wiring prompt, which will also decide whether `SpendRepository` moves to
`dal/ports.py` alongside `GovernanceRepository`/`AuditRepository`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from .errors import (
    CeilingExceeded,
    EnvelopeNotFound,
    ReservationConflict,
)
from .models import Reservation, SpendEnvelope

DEFAULT_HOLD_TTL = timedelta(minutes=15)


@runtime_checkable
class SpendRepository(Protocol):
    """Port. The asyncpg implementation below is one adapter; tests use a fake.

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
    ) -> Reservation | None:
        """Atomically place a hold. Returns None iff the ceiling would be breached.
        Raises `EnvelopeNotFound` if no active envelope exists."""

    async def commit(
        self, *, org_id: str, reservation_id: UUID, actual_minor: int, now: datetime
    ) -> None: ...

    async def release(
        self, *, org_id: str, reservation_id: UUID, now: datetime
    ) -> None: ...

    async def get_envelope(
        self, *, org_id: str, principal_id: str, now: datetime
    ) -> SpendEnvelope | None: ...

    async def sweep_expired(self, *, now: datetime, limit: int = 500) -> int: ...


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
        )

    async def release(
        self, *, org_id: str, reservation_id: UUID, now: datetime | None = None
    ) -> None:
        await self._repo.release(
            org_id=org_id,
            reservation_id=reservation_id,
            now=now or datetime.now(timezone.utc),
        )


# --------------------------------------------------------------------------- #
# asyncpg adapter
# --------------------------------------------------------------------------- #

_RESERVE_SQL = """
WITH target AS (
    SELECT envelope_id
      FROM spend_envelope
     WHERE org_id = $1
       AND principal_id = $2
       AND revoked_at IS NULL
       AND $6 >= period_start
       AND $6 <  period_end
     FOR UPDATE
),
bumped AS (
    UPDATE spend_envelope e
       SET reserved_minor = e.reserved_minor + $3
      FROM target t
     WHERE e.envelope_id = t.envelope_id
       AND e.spent_minor + e.reserved_minor + $3 <= e.ceiling_minor
    RETURNING e.envelope_id
)
INSERT INTO spend_reservation (
    reservation_id, envelope_id, org_id, idempotency_key, amount_minor,
    correlation_id, governance_token_id, state, created_at, expires_at
)
SELECT $4, b.envelope_id, $1, $5, $3, $8, $9, 'held', $6, $7
  FROM bumped b
ON CONFLICT (org_id, idempotency_key) DO NOTHING
RETURNING reservation_id, envelope_id, amount_minor, created_at, expires_at;
"""


class PostgresSpendRepository:
    """asyncpg adapter.

    Only asyncpg public API is used: `pool.acquire()`, `conn.transaction()`,
    `conn.fetchrow()`, `conn.execute()`.

    RLS: `set_config('skylize.org_id', $1, true)` is the parameterised
    (injection-safe) equivalent of `SET LOCAL`; `true` scopes it to the
    transaction. Confirmed against HEAD (PROMPT 0 audit, A9): this is the exact
    GUC name used by every existing RLS policy (dal/connection.py:79,
    migrations/versions/0007_org_credentials.py:66-67, and 8 other migrations).
    """

    def __init__(self, pool: object, *, rls_guc: str = "skylize.org_id") -> None:
        self._pool = pool
        self._rls_guc = rls_guc

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
    ) -> Reservation | None:
        reservation_id = uuid4()
        async with self._pool.acquire() as conn:  # type: ignore[attr-defined]
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config($1, $2, true)", self._rls_guc, org_id
                )
                row = await conn.fetchrow(
                    _RESERVE_SQL,
                    org_id,
                    principal_id,
                    amount_minor,
                    reservation_id,
                    idempotency_key,
                    now,
                    expires_at,
                    correlation_id,
                    governance_token_id,
                )
                if row is None:
                    # Either the ceiling blocked it, or the idempotency key already
                    # exists. Distinguish, because replaying a retried request must
                    # return the ORIGINAL hold rather than denying.
                    existing = await conn.fetchrow(
                        """
                        SELECT reservation_id, envelope_id, amount_minor, state,
                               created_at, expires_at, committed_minor
                          FROM spend_reservation
                         WHERE org_id = $1 AND idempotency_key = $2
                        """,
                        org_id,
                        idempotency_key,
                    )
                    if existing is None:
                        return None  # genuine ceiling denial
                    if existing["amount_minor"] != amount_minor:
                        raise ReservationConflict(
                            f"idempotency_key {idempotency_key!r} already held for "
                            f"{existing['amount_minor']}, not {amount_minor}"
                        )
                    return Reservation(
                        reservation_id=existing["reservation_id"],
                        envelope_id=existing["envelope_id"],
                        org_id=org_id,
                        idempotency_key=idempotency_key,
                        amount_minor=existing["amount_minor"],
                        correlation_id=correlation_id,
                        governance_token_id=governance_token_id,
                        state=existing["state"],
                        created_at=existing["created_at"],
                        expires_at=existing["expires_at"],
                        committed_minor=existing["committed_minor"],
                    )
                return Reservation(
                    reservation_id=row["reservation_id"],
                    envelope_id=row["envelope_id"],
                    org_id=org_id,
                    idempotency_key=idempotency_key,
                    amount_minor=row["amount_minor"],
                    correlation_id=correlation_id,
                    governance_token_id=governance_token_id,
                    state="held",
                    created_at=row["created_at"],
                    expires_at=row["expires_at"],
                )

    async def commit(
        self, *, org_id: str, reservation_id: UUID, actual_minor: int, now: datetime
    ) -> None:
        async with self._pool.acquire() as conn:  # type: ignore[attr-defined]
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config($1, $2, true)", self._rls_guc, org_id
                )
                row = await conn.fetchrow(
                    """
                    UPDATE spend_reservation
                       SET state = 'committed',
                           committed_minor = LEAST($3, amount_minor),
                           settled_at = $4
                     WHERE reservation_id = $1 AND org_id = $2 AND state = 'held'
                    RETURNING envelope_id, amount_minor, committed_minor
                    """,
                    reservation_id,
                    org_id,
                    actual_minor,
                    now,
                )
                if row is None:
                    return  # already settled — commit is idempotent
                await conn.execute(
                    """
                    UPDATE spend_envelope
                       SET reserved_minor = reserved_minor - $2,
                           spent_minor    = spent_minor + $3
                     WHERE envelope_id = $1
                    """,
                    row["envelope_id"],
                    row["amount_minor"],
                    row["committed_minor"],
                )

    async def release(self, *, org_id: str, reservation_id: UUID, now: datetime) -> None:
        async with self._pool.acquire() as conn:  # type: ignore[attr-defined]
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config($1, $2, true)", self._rls_guc, org_id
                )
                row = await conn.fetchrow(
                    """
                    UPDATE spend_reservation
                       SET state = 'released', settled_at = $3
                     WHERE reservation_id = $1 AND org_id = $2 AND state = 'held'
                    RETURNING envelope_id, amount_minor
                    """,
                    reservation_id,
                    org_id,
                    now,
                )
                if row is None:
                    return
                await conn.execute(
                    "UPDATE spend_envelope SET reserved_minor = reserved_minor - $2 "
                    "WHERE envelope_id = $1",
                    row["envelope_id"],
                    row["amount_minor"],
                )

    async def get_envelope(
        self, *, org_id: str, principal_id: str, now: datetime
    ) -> SpendEnvelope | None:
        # The `set_config(..., true)` MUST sit inside an explicit transaction, as
        # `Database.tenant_session` does (dal/connection.py:70-81). `is_local=true`
        # scopes the GUC to the surrounding transaction; with no transaction open,
        # asyncpg runs each statement in its own implicit one, so the setting is
        # discarded the instant it is set and the SELECT below runs with
        # `skylize.org_id` empty. Under RLS as the non-superuser `skylize_app`
        # role that matched no rows and this returned None for an envelope that
        # exists -- which made every ceiling denial surface as `EnvelopeNotFound`
        # and left `CeilingExceeded.defer_to_human` unreachable in production.
        #
        # The column list is explicit rather than `SELECT *`: `SpendEnvelope` is
        # `extra="forbid"` and the table carries a `created_at` the model does not
        # declare (migration 0019), so `SELECT *` raises ValidationError here.
        async with self._pool.acquire() as conn:  # type: ignore[attr-defined]
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config($1, $2, true)", self._rls_guc, org_id
                )
                row = await conn.fetchrow(
                    """
                    SELECT envelope_id, org_id, principal_id, currency,
                           ceiling_minor, reserved_minor, spent_minor,
                           period_start, period_end, over_ceiling_behavior,
                           revoked_at
                      FROM spend_envelope
                     WHERE org_id = $1 AND principal_id = $2 AND revoked_at IS NULL
                       AND $3 >= period_start AND $3 < period_end
                    """,
                    org_id,
                    principal_id,
                    now,
                )
                return SpendEnvelope(**dict(row)) if row is not None else None

    async def sweep_expired(self, *, now: datetime, limit: int = 500) -> int:
        """Release abandoned holds. Run from Temporal on a schedule, not from a
        request path. Returns the number of holds reclaimed."""
        async with self._pool.acquire() as conn:  # type: ignore[attr-defined]
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    UPDATE spend_reservation
                       SET state = 'expired', settled_at = $1
                     WHERE reservation_id IN (
                           SELECT reservation_id FROM spend_reservation
                            WHERE state = 'held' AND expires_at < $1
                            ORDER BY expires_at LIMIT $2
                            FOR UPDATE SKIP LOCKED)
                    RETURNING envelope_id, amount_minor
                    """,
                    now,
                    limit,
                )
                for row in rows:
                    await conn.execute(
                        "UPDATE spend_envelope SET reserved_minor = reserved_minor - $2 "
                        "WHERE envelope_id = $1",
                        row["envelope_id"],
                        row["amount_minor"],
                    )
                return len(rows)
