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
what. A missing (org, period) row means the gate FAILS CLOSED (owner decision
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
        """The org-wide ceiling in micro-USD for (org, period), or None if unset.

        ``None`` means NO ceiling row exists for this (org, period) — the honest
        fail-closed signal (owner decision D6); it is deliberately distinct from a
        configured ceiling of ``0`` (which permits no spend). Tenant-scoped via RLS.
        """
        async with self._db.tenant_session(org_id) as conn:
            value = await conn.fetchval(
                """
                SELECT ceiling_micros
                FROM org_spend_ceiling
                WHERE org_id = $1 AND billing_period = $2
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
