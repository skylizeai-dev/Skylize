"""gcp_containment_claims repository — the durable mutex behind the auto-hook.

See migration 0025 for the full design rationale. In one line: `try_claim` is an
`INSERT ... ON CONFLICT` whose conflict-target `(org_id, label)` PRIMARY KEY is
what makes two replicas racing the same breach resolve to exactly one winner —
Postgres serializes the conflicting write itself, so nothing in this module (or
its caller) needs its own lock.

RLS-scoped like every other GCP table (0024, 0025): every statement runs inside
`Database.tenant_session(org_id)` and ALSO carries an explicit `org_id`
predicate, the same belt-and-braces discipline `dal/gcp_wif.py` and
`dal/oauth_credentials.py` already use.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class GcpContainmentClaimRow:
    org_id: str
    label: str
    hitl_id: UUID | None
    claimed_at: datetime


class GcpContainmentClaimRepository(Protocol):
    async def try_claim(
        self, *, org_id: str, label: str, stale_before: datetime
    ) -> bool:
        """Atomically reserve the (org_id, label) slot, or refuse.

        Returns True iff THIS call now owns the slot. A prior claim is reclaimed
        only when it is stale: either it names a `hitl_id` no longer `'pending'`
        in `hitl_queue` (a human already decided), or it never got one and is
        older than `stale_before` (the crash-recovery backstop — see migration
        0025). Every other case refuses, which is the race-safety guarantee: of
        two concurrent callers, at most one sees True.
        """
        ...

    async def record_hitl_id(
        self, *, org_id: str, label: str, hitl_id: UUID
    ) -> None:
        """Attach the real ticket to a slot this caller just claimed.

        Called ONLY after `execute()` returns the ticket a deferred proposal
        produced, so a LATER staleness check can find it and release the slot
        the moment a human decides — instead of waiting out the crash backstop.
        """
        ...

    async def release(self, *, org_id: str, label: str) -> None:
        """Drop the claim outright — the proposal attempt did not produce a
        pending ticket (a governance denial, a database fault, an unexpected
        auto-approval). Releasing immediately, rather than waiting for
        `stale_before` to catch up, is what lets the NEXT breach retry a
        genuinely fixable condition without an arbitrary wait."""
        ...


# ---------------------------------------------------------------------------
# asyncpg implementation
# ---------------------------------------------------------------------------

_TRY_CLAIM_SQL = """
INSERT INTO gcp_containment_claims (org_id, label, hitl_id, claimed_at)
VALUES ($1, $2, NULL, now())
ON CONFLICT (org_id, label) DO UPDATE
   SET hitl_id = NULL, claimed_at = now()
 WHERE
   CASE
     WHEN gcp_containment_claims.hitl_id IS NULL THEN
       -- No ticket was ever recorded: either a genuinely concurrent claim
       -- still mid-flight (must NOT be stolen) or a crashed one (must
       -- eventually be). Only the second case may reclaim, so this branch
       -- checks age, never "hitl_id IS NULL" alone.
       gcp_containment_claims.claimed_at < $3
     ELSE
       -- A ticket exists: the slot is free again the moment it stops being
       -- 'pending', whether that took ten seconds or ten minutes.
       NOT EXISTS (
         SELECT 1 FROM hitl_queue h
          WHERE h.hitl_id = gcp_containment_claims.hitl_id
            AND h.org_id = gcp_containment_claims.org_id
            AND h.status = 'pending'
       )
   END
RETURNING org_id
"""


class PgGcpContainmentClaimRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def try_claim(
        self, *, org_id: str, label: str, stale_before: datetime
    ) -> bool:
        async with self._db.tenant_session(org_id) as conn:
            row = await conn.fetchrow(_TRY_CLAIM_SQL, org_id, label, stale_before)
            return row is not None

    async def record_hitl_id(
        self, *, org_id: str, label: str, hitl_id: UUID
    ) -> None:
        async with self._db.tenant_session(org_id) as conn:
            await conn.execute(
                "UPDATE gcp_containment_claims "
                "SET hitl_id=$3, claimed_at=now() WHERE org_id=$1 AND label=$2",
                org_id, label, hitl_id,
            )

    async def release(self, *, org_id: str, label: str) -> None:
        async with self._db.tenant_session(org_id) as conn:
            await conn.execute(
                "DELETE FROM gcp_containment_claims WHERE org_id=$1 AND label=$2",
                org_id, label,
            )


# ---------------------------------------------------------------------------
# In-memory implementation (tests / memory backend)
# ---------------------------------------------------------------------------

class InMemoryGcpContainmentClaimRepository:
    """Test/memory-backend twin.

    DELIBERATELY SIMPLER than the Pg implementation: it has no `hitl_queue` to
    join against (the memory backend's HITL store is a separate, unrelated
    object with no wiring between them here, and inventing one would be new
    cross-repository coupling this pass does not need). A claim is therefore
    reclaimable ONLY via the crash-timeout backstop, never early on a human
    decision. That is a conservative divergence from the Pg behaviour, not an
    unsafe one — the memory backend is single-process dev/test surface where
    the cross-replica race this table exists for cannot occur in the first
    place. The early-release behaviour is proven against the real repository in
    `tests/integration/test_containment_claims_pg.py`, not here.
    """

    def __init__(self) -> None:
        self._claims: dict[tuple[str, str], GcpContainmentClaimRow] = {}

    async def try_claim(
        self, *, org_id: str, label: str, stale_before: datetime
    ) -> bool:
        key = (org_id, label)
        existing = self._claims.get(key)
        if existing is not None and not (
            existing.hitl_id is None and existing.claimed_at < stale_before
        ):
            return False
        self._claims[key] = GcpContainmentClaimRow(
            org_id=org_id, label=label, hitl_id=None,
            claimed_at=datetime.now(timezone.utc),
        )
        return True

    async def record_hitl_id(
        self, *, org_id: str, label: str, hitl_id: UUID
    ) -> None:
        key = (org_id, label)
        existing = self._claims.get(key)
        if existing is None:
            return
        self._claims[key] = replace(existing, hitl_id=hitl_id)

    async def release(self, *, org_id: str, label: str) -> None:
        self._claims.pop((org_id, label), None)
