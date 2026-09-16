"""asyncpg adapter for `SpendRepository` (app/principal/spend.py).

CONNECTION-PATTERN NOTE (not fixed here, flagged for a future wiring pass):
`dal/connection.py` documents itself as "the ONLY module that opens an asyncpg
connection," and every other DAL class takes a `Database` and runs its
tenant-scoped queries inside `Database.tenant_session(org_id)` rather than
acquiring a pool and calling `set_config` itself (see dal/cost_ledger.py,
dal/org_spend_ceiling.py). `PostgresSpendRepository` below still does the
latter; only its *placement* (out of `skylize.app.principal`, where it could
not legally catch `asyncpg.UniqueViolationError` -- pyproject.toml's
"Application logic contains no SQL (depends on dal ports only)" import-linter
contract forbids any transitive `skylize.app -> asyncpg` path, not just a
direct one) is what this module fixes.

PROMPT 0 AUDIT NOTE (driver + RLS GUC): confirmed asyncpg is the runtime driver
(pyproject.toml — asyncpg>=0.29; SQLAlchemy is Alembic-only) and the RLS GUC used
by every existing policy is `skylize.org_id`, not `app.org_id` (e.g.
migrations/versions/0007_org_credentials.py, dal/connection.py:79).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from ..app.principal.errors import ReplayKeyConflict, ReservationConflict
from ..app.principal.models import Reservation, SpendEnvelope


def _loads(raw: Any) -> dict[str, Any] | None:
    """Decode a JSONB column that asyncpg hands back as text.

    Tolerates an already-decoded mapping so the helper keeps working if a JSON
    type codec is ever registered on the pool.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    decoded = json.loads(raw)
    return decoded if isinstance(decoded, dict) else None


# THE INSERT DRIVES THE BUMP, never the other way round.
#
# An earlier shape ran the `reserved_minor` UPDATE in its own CTE and fed the
# INSERT from it. That is correct only while every idempotency_key is unique: on
# a REPEATED key the UPDATE had already applied by the time `ON CONFLICT DO
# NOTHING` dropped the insert, so the envelope carried a hold with no
# `spend_reservation` row behind it. Nothing could ever reclaim it — `release`
# and `commit` both work from a reservation row, and `sweep_expired` scans the
# same table — so the org silently lost that much ceiling until the period
# rolled. Measured against PostgreSQL 16, a 10000-minor reservation replayed
# once left `reserved_minor = 20000` with 10000 backed by rows.
#
# Ordering the CTEs this way makes the leak unrepresentable rather than merely
# unlikely: `bumped` reads from `ins`, so a conflicting insert returns no row and
# bumps nothing. That also settles the concurrent case, where a NOT EXISTS
# pre-check would not — a racing transaction's uncommitted row is invisible to a
# pre-check, but its own INSERT still loses the conflict and so still bumps
# nothing.
#
# `FOR UPDATE` in `target` is what serializes two reservations against the same
# envelope, so the ceiling test below reads a value nobody else can be mutating.
_RESERVE_SQL = """
WITH target AS (
    SELECT envelope_id, ceiling_minor, reserved_minor, spent_minor
      FROM spend_envelope
     WHERE org_id = $1
       AND principal_id = $2
       AND revoked_at IS NULL
       AND $6 >= period_start
       AND $6 <  period_end
     FOR UPDATE
),
ins AS (
    INSERT INTO spend_reservation (
        reservation_id, envelope_id, org_id, idempotency_key, amount_minor,
        correlation_id, governance_token_id, state, created_at, expires_at,
        replay_key
    )
    SELECT $4, t.envelope_id, $1, $5, $3, $8, $9, 'held', $6, $7, $10
      FROM target t
     WHERE t.spent_minor + t.reserved_minor + $3 <= t.ceiling_minor
    ON CONFLICT (org_id, idempotency_key) DO NOTHING
    RETURNING reservation_id, envelope_id, amount_minor, created_at, expires_at
),
bumped AS (
    UPDATE spend_envelope e
       SET reserved_minor = e.reserved_minor + $3
      FROM ins i
     WHERE e.envelope_id = i.envelope_id
    RETURNING e.envelope_id
)
SELECT reservation_id, envelope_id, amount_minor, created_at, expires_at
  FROM ins;
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
        replay_key: UUID | None = None,
    ) -> Reservation | None:
        reservation_id = uuid4()
        async with self._pool.acquire() as conn:  # type: ignore[attr-defined]
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config($1, $2, true)", self._rls_guc, org_id
                )
                try:
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
                        replay_key,
                    )
                except asyncpg.UniqueViolationError as exc:
                    # THE RACE `ON CONFLICT (org_id, idempotency_key)` does not
                    # cover. `idempotency_key` is fresh per attempt even on a
                    # replay, so it never collides here -- but `replay_key` can:
                    # a concurrent attempt for the SAME replay key (e.g. a
                    # duplicate delivery of one HITL-approval resume) can insert
                    # its 'held' row in the gap between this call's
                    # `find_replay` (which found nothing live) and this INSERT.
                    # `spend_reservation_replay_live`
                    # (migrations/versions/0028_spend_reservation_replay_key.py)
                    # is the only unique index this INSERT can hit that is not
                    # already handled by `ON CONFLICT`, so any UniqueViolation
                    # reaching here is that race, not a fresh kind of failure.
                    #
                    # Raised rather than swallowed: the caller already lost the
                    # race, so there is nothing to retry into -- the correct
                    # answer is to deny this attempt exactly as `find_replay`
                    # would have if it had run a moment later and seen the
                    # winner's row. `ToolProxy._reserve_spend` maps this onto
                    # `ToolSpendReplayInFlight`, the SAME type `_replay_guard`
                    # raises for a `held` row found by `find_replay` -- from the
                    # caller's perspective these are one situation (a replay is
                    # already in flight), only caught at two different points.
                    raise ReplayKeyConflict(
                        f"replay_key {replay_key} already backs a live "
                        f"reservation for org {org_id!r}; lost the race between "
                        f"find_replay and try_reserve: {exc}"
                    ) from exc
                if row is None:
                    # Either the ceiling blocked it, or the idempotency key already
                    # exists. Distinguish, because replaying a retried request must
                    # return the ORIGINAL hold rather than denying.
                    existing = await conn.fetchrow(
                        """
                        SELECT reservation_id, envelope_id, amount_minor, state,
                               created_at, expires_at, committed_minor,
                               replay_key, result_snapshot
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
                        replay_key=existing["replay_key"],
                        result_snapshot=_loads(existing["result_snapshot"]),
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
                    replay_key=replay_key,
                )

    async def commit(
        self,
        *,
        org_id: str,
        reservation_id: UUID,
        actual_minor: int,
        now: datetime,
        result_snapshot: dict[str, Any] | None = None,
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
                           settled_at = $4,
                           -- COALESCE, not assignment: commit is idempotent and
                           -- must never blank a snapshot a first settlement
                           -- already recorded.
                           result_snapshot = COALESCE($5::jsonb, result_snapshot)
                     WHERE reservation_id = $1 AND org_id = $2 AND state = 'held'
                    RETURNING envelope_id, amount_minor, committed_minor
                    """,
                    reservation_id,
                    org_id,
                    actual_minor,
                    now,
                    None if result_snapshot is None
                    else json.dumps(result_snapshot, default=str),
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

    async def find_by_replay_key(
        self, *, org_id: str, replay_key: UUID
    ) -> Reservation | None:
        """Read the live-or-settled reservation for a replay key.

        The `state IN ('held','committed')` filter is the SAME predicate as
        migration 0028's partial unique index, which is what guarantees this
        returns at most one row. `released` and `expired` rows are intentionally
        invisible here: those states free the key for a genuine re-attempt.
        """
        async with self._pool.acquire() as conn:  # type: ignore[attr-defined]
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config($1, $2, true)", self._rls_guc, org_id
                )
                row = await conn.fetchrow(
                    """
                    SELECT reservation_id, envelope_id, idempotency_key,
                           amount_minor, correlation_id, governance_token_id,
                           state, created_at, expires_at, committed_minor,
                           replay_key, result_snapshot
                      FROM spend_reservation
                     WHERE org_id = $1 AND replay_key = $2
                       AND state IN ('held', 'committed')
                    """,
                    org_id,
                    replay_key,
                )
        if row is None:
            return None
        return Reservation(
            reservation_id=row["reservation_id"],
            envelope_id=row["envelope_id"],
            org_id=org_id,
            idempotency_key=row["idempotency_key"],
            amount_minor=row["amount_minor"],
            correlation_id=row["correlation_id"],
            governance_token_id=row["governance_token_id"],
            state=row["state"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            committed_minor=row["committed_minor"],
            replay_key=row["replay_key"],
            result_snapshot=_loads(row["result_snapshot"]),
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

    async def sweep_expired(
        self, *, org_ids: Sequence[str], now: datetime, limit: int = 500
    ) -> int:
        """Release abandoned holds. Run from Temporal on a schedule, not from a
        request path. Returns the number of holds reclaimed.

        `spend_reservation` is FORCE RLS (migration 0019): a query with no
        `skylize.org_id` GUC set matches no rows under the non-superuser
        `skylize_app` role, not an error. So this binds the GUC once per org in
        `org_ids` and sweeps each org's transaction separately, rather than
        issuing one unscoped query that would silently reclaim nothing."""
        reclaimed = 0
        for org_id in org_ids:
            async with self._pool.acquire() as conn:  # type: ignore[attr-defined]
                async with conn.transaction():
                    await conn.execute(
                        "SELECT set_config($1, $2, true)", self._rls_guc, org_id
                    )
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
                    reclaimed += len(rows)
        return reclaimed
