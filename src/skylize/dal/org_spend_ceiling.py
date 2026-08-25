"""Org Spend Ceiling DAL — the org-wide LLM money spend ceiling (owner D1-D6).

This is the read/write layer for ``org_spend_ceiling`` (migration 0014): the
NEW first-class object holding the org-wide LLM spend ceiling in **micro-USD**
(millionths of one USD — the SAME unit as ``ai_cost_ledger.cost_micros``,
ADR-0006; owner decision D3). It is NOT ``budget_ledger`` and imports nothing
from ``skylize.decision_engine`` (owner decision D1).

Both queries run inside ``Database.tenant_session(org_id)`` so the RLS
``tenant_isolation`` policy on ``org_spend_ceiling`` applies — one org can never
read or write another org's ceiling — exactly as ``CostLedgerDAL`` is scoped.

The table is MUTABLE CONFIG, not append-only (owner decision D5). A ceiling
change is a governance event, not silent config (owner decision, step 11): the
setter writes an ``AuditService`` record on every change, so the ``audit_log``
trail (and the audit event on the bus) always carries who changed the ceiling to
what.

The read is EFFECTIVE-DATED (owner decision, Stage 1): it resolves to the ceiling
of the GREATEST ``billing_period`` at or before the requested period, so a
configured org no longer goes dark at each calendar-month rollover (mirroring the
effective-dated pricing precedent set by migration 0013's two Sonnet 5 rows).
Only the ceiling LOOKUP is effective-dated — spend accounting is untouched and
never carries across months (period-to-date spend is still aggregated for the
current calendar month by ``CostLedgerDAL.org_period_total_micros``). When NO row
exists at or before the requested period the gate FAILS CLOSED (owner decision
D6); this DAL never fabricates a default — a ``None`` read is the honest
"no ceiling configured" signal the caller refuses on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from skylize.app.audit.service import AuditService
    from skylize.dal.connection import Database


class OrgSpendCeilingDAL:
    def __init__(self, db: "Database") -> None:
        self._db = db

    async def read_ceiling_micros(
        self, org_id: str, billing_period: str
    ) -> int | None:
        """The org-wide ceiling in micro-USD IN FORCE for (org, period), or None.

        EFFECTIVE-DATED (owner decision, Stage 1): the ceiling resolves to the row
        with the GREATEST ``billing_period`` that is ``<=`` the requested period —
        NOT an exact-month match. A ceiling set in an earlier month therefore stays
        in force in every later month until a newer row supersedes it, so a
        configured org no longer goes dark at each calendar-month rollover. This
        mirrors the effective-dated pricing precedent set by migration 0013's two
        Sonnet 5 ``model_pricing`` rows.

        Resolution semantics:
          * NO row at or before the requested period -> ``None`` -> caller REFUSES.
            Fail-closed for a never-configured org is PRESERVED (owner decision D6):
            ``None`` is still the honest "no ceiling configured" signal, distinct
            from a configured ceiling of ``0`` (which permits no spend).
          * a row exists only for an EARLIER period  -> that ceiling is in force.
          * a NEWER row supersedes an older one from its own period onward.
          * a row for a period LATER than the requested one is NOT used — the
            ``<=`` predicate excludes future-dated ceilings.

        Spend accounting is UNCHANGED and does NOT carry over: only this ceiling
        LOOKUP is effective-dated. Period-to-date spend is still aggregated for the
        CURRENT calendar month by ``CostLedgerDAL.org_period_total_micros`` (the
        enforcer compares that current-month spend against the effective ceiling);
        an earlier month's ceiling never drags an earlier month's spend forward.

        Tenant-scoped via RLS. The ``(org_id, billing_period)`` primary-key btree
        already serves this predicate (leading equality on ``org_id`` plus a
        reverse range scan on ``billing_period``), so no new index — and no new
        migration — is added.
        """
        async with self._db.tenant_session(org_id) as conn:
            value = await conn.fetchval(
                """
                SELECT ceiling_micros
                FROM org_spend_ceiling
                WHERE org_id = $1 AND billing_period <= $2
                ORDER BY billing_period DESC
                LIMIT 1
                """,
                org_id,
                billing_period,
            )
        return int(value) if value is not None else None

    async def set_ceiling(
        self,
        *,
        org_id: str,
        billing_period: str,
        ceiling_micros: int,
        audit: "AuditService",
        correlation_id: UUID,
        source_agent_id: str | None = None,
        governance_token_id: UUID | None = None,
    ) -> None:
        """Set (upsert) the org-wide ceiling in micro-USD for (org, period).

        MUTABLE CONFIG (D5): idempotent upsert on the (org_id, billing_period)
        primary key, refreshing ``updated_at``. Tenant-scoped via RLS, so a caller
        can only write its own org's row (the WITH CHECK policy also enforces it).

        A ceiling change is a GOVERNANCE EVENT, not silent config (step 11): after
        the write commits, this setter records an ``AuditService`` action
        (``governance.spend_ceiling_set``) carrying the before/after value, so the
        append-only audit trail always shows who moved the ceiling and to what.

        ``ceiling_micros`` is MICRO-USD (D3) and must be >= 0 (the same invariant
        the DB CHECK enforces); a negative value is rejected before the write.
        """
        if ceiling_micros < 0:
            raise ValueError(
                f"ceiling_micros must be >= 0 (micro-USD); got {ceiling_micros}"
            )
        async with self._db.tenant_session(org_id) as conn:
            previous = await conn.fetchval(
                """
                SELECT ceiling_micros
                FROM org_spend_ceiling
                WHERE org_id = $1 AND billing_period = $2
                """,
                org_id,
                billing_period,
            )
            await conn.execute(
                """
                INSERT INTO org_spend_ceiling (org_id, billing_period, ceiling_micros)
                VALUES ($1, $2, $3)
                ON CONFLICT (org_id, billing_period)
                DO UPDATE SET ceiling_micros = EXCLUDED.ceiling_micros,
                              updated_at = now()
                """,
                org_id,
                billing_period,
                ceiling_micros,
            )
        await audit.record(
            org_id=org_id,
            correlation_id=correlation_id,
            action_type="governance.spend_ceiling_set",
            result="success",
            source_agent_id=source_agent_id,
            governance_token_id=governance_token_id,
            result_reason=(
                f"billing_period={billing_period} "
                f"ceiling_micros {previous} -> {ceiling_micros}"
            ),
            inputs={"billing_period": billing_period, "ceiling_micros": ceiling_micros},
        )
