"""Org Autonomy Mode DAL — the ORG-WIDE autonomy posture (rulings 6 and 7).

Read/write layer for ``org_autonomy_mode`` (migration 0028): which of the five
``skylize.contracts.base.AutonomyMode`` values is in force for an org, and
therefore how much of what its agents propose executes without a person.

ORG-WIDE (ruling 6). There is no per-department or per-agent read here because
there is no per-department or per-agent row; a narrower setting would let one
corner of the org run hotter than its owner believes.

Both queries run inside ``Database.tenant_session(org_id)`` so the RLS
``tenant_isolation`` policy applies — one org can never read or write another
org's posture — exactly as ``OrgSpendCeilingDAL`` is scoped.

The read is EFFECTIVE-DATED, the shape ``OrgSpendCeilingDAL.read_ceiling_micros``
established: the mode in force is the row with the greatest ``effective_from``
at or before the requested instant, so a mode set once stays in force until a
newer row supersedes it, and the old row stays as history.

FAIL CLOSED, NO EXCEPTIONS (ruling 7): when no row resolves, ``read_mode``
returns ``observe``. It does not return None, and it has no parameter that can
turn that off. An org whose posture nobody has set yet gets the mode where every
action needs a human. ``read_configured_mode`` exists alongside it for the one
caller that must tell "unset" from "explicitly observe" — a console showing the
owner whether they have ever made this choice — and enforcement must never use
it: it returns None, and None is not a posture.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast
from uuid import UUID

from skylize.contracts.base import DEFAULT_AUTONOMY_MODE, AutonomyMode

if TYPE_CHECKING:
    from skylize.app.audit.service import AuditService
    from skylize.dal.connection import Database

#: The five valid modes, mirroring the CHECK constraint in migration 0028 and
#: the Literal in contracts/base.py. Validated before the write so a bad value
#: fails with a clear error rather than a raw constraint violation.
VALID_AUTONOMY_MODES: frozenset[str] = frozenset(
    {"observe", "propose", "act_within_budget", "act_and_reallocate", "act_governed"}
)


class OrgAutonomyModeDAL:
    def __init__(self, db: "Database") -> None:
        self._db = db

    async def read_mode(
        self, org_id: str, at: datetime | None = None
    ) -> AutonomyMode:
        """The org-wide autonomy mode IN FORCE, fail-closed to ``observe``.

        Resolution semantics, mirroring the effective-dated ceiling read:
          * NO row at or before ``at`` -> ``observe`` (ruling 7). This is the
            whole point of the method: enforcement asks "what may run without a
            human" and always gets an answer it can act on safely.
          * a row exists only EARLIER -> that mode is in force.
          * a NEWER row supersedes an older one from its own instant onward.
          * a row effective LATER than ``at`` is NOT used.

        ``at`` defaults to now (UTC). Tenant-scoped via RLS. The
        ``(org_id, effective_from)`` primary-key btree already serves this
        predicate — leading equality on ``org_id``, reverse range scan on
        ``effective_from`` — so no extra index and no extra migration.
        """
        value = await self.read_configured_mode(org_id, at)
        return value if value is not None else DEFAULT_AUTONOMY_MODE

    async def read_configured_mode(
        self, org_id: str, at: datetime | None = None
    ) -> AutonomyMode | None:
        """The mode in force, or None when the org has never set one.

        For DISPLAY only — a console telling an owner whether they have made
        this choice yet. Enforcement must call ``read_mode``, which cannot
        return None. Same effective-dated resolution, same RLS scoping.
        """
        moment = at if at is not None else datetime.now(timezone.utc)
        async with self._db.tenant_session(org_id) as conn:
            value = await conn.fetchval(
                """
                SELECT autonomy_mode
                FROM org_autonomy_mode
                WHERE org_id = $1 AND effective_from <= $2
                ORDER BY effective_from DESC
                LIMIT 1
                """,
                org_id,
                moment,
            )
        if value is None:
            return None
        # The CHECK constraint in migration 0028 is what makes this cast safe:
        # the column cannot hold a string outside AutonomyMode.
        return cast(AutonomyMode, value)

    async def set_mode(
        self,
        *,
        org_id: str,
        autonomy_mode: AutonomyMode,
        audit: "AuditService",
        correlation_id: UUID,
        effective_from: datetime | None = None,
        source_agent_id: str | None = None,
        governance_token_id: UUID | None = None,
    ) -> AutonomyMode:
        """Set the org-wide autonomy mode, effective at ``effective_from``.

        Mutable config: an idempotent upsert on the ``(org_id, effective_from)``
        primary key, refreshing ``updated_at``. Tenant-scoped via RLS, so a
        caller can only write its own org's row; the WITH CHECK half of the
        policy enforces that on the write as well as the read.

        A posture change is a GOVERNANCE EVENT, not silent config — the same
        rule ``OrgSpendCeilingDAL.set_ceiling`` follows for the spend ceiling.
        After the write commits this records a ``governance.autonomy_mode_set``
        audit action carrying the before/after value, so the append-only trail
        always shows who changed how much the org's agents may do alone.

        The mode is validated here as well as by the DB CHECK, so a bad value
        fails with a legible error instead of a constraint violation. Returns
        the mode now in force at ``effective_from``.
        """
        if autonomy_mode not in VALID_AUTONOMY_MODES:
            raise ValueError(
                f"autonomy_mode must be one of {sorted(VALID_AUTONOMY_MODES)}; "
                f"got {autonomy_mode!r}"
            )
        moment = (
            effective_from if effective_from is not None else datetime.now(timezone.utc)
        )
        previous = await self.read_configured_mode(org_id, moment)
        async with self._db.tenant_session(org_id) as conn:
            await conn.execute(
                """
                INSERT INTO org_autonomy_mode (org_id, effective_from, autonomy_mode)
                VALUES ($1, $2, $3)
                ON CONFLICT (org_id, effective_from)
                DO UPDATE SET autonomy_mode = EXCLUDED.autonomy_mode,
                              updated_at = now()
                """,
                org_id,
                moment,
                autonomy_mode,
            )
        await audit.record(
            org_id=org_id,
            correlation_id=correlation_id,
            action_type="governance.autonomy_mode_set",
            result="success",
            source_agent_id=source_agent_id,
            governance_token_id=governance_token_id,
            result_reason=(
                f"effective_from={moment.isoformat()} "
                f"autonomy_mode {previous} -> {autonomy_mode}"
            ),
            inputs={
                "effective_from": moment.isoformat(),
                "autonomy_mode": autonomy_mode,
            },
        )
        return autonomy_mode
