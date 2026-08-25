"""Spend position read route — org spend against the ceiling, for the console.

Read-only. Mirrors the audit read route's conventions (edge/routes/audit.py):
RBAC via require_any_role_or_user("owner", "admin"), a typed Pydantic response
model, and org_id strictly from the authenticated RequestContext — never a
query parameter or body field. Both data sources are RLS-scoped DALs: the
org-wide period-to-date aggregate (CostLedgerDAL.org_period_total_micros) and
the effective-dated ceiling (OrgSpendCeilingDAL.read_ceiling_micros). Setting a
ceiling stays an operator action through the audited set_ceiling seam — there
is deliberately no write endpoint here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...bootstrap import Container
from ...schemas.base import RequestContext
from ..deps import get_container, require_any_role_or_user

router = APIRouter(prefix="/api/v1/spend", tags=["spend"])


class SpendPositionResponse(BaseModel):
    billing_period: str = Field(
        description="Current calendar month (UTC), e.g. '2026-07' — the same "
        "period convention the LLM egress gate and the cost ledger use."
    )
    period_to_date_micros: int = Field(
        description="Org-wide spend this period in micro-USD, across all "
        "providers, charges net of reversals."
    )
    ceiling_configured: bool = Field(
        description="False when no ceiling row resolves for the org at or "
        "before this period. The gate fails closed (D6): every LLM call is "
        "refused until an operator sets a ceiling."
    )
    ceiling_micros: int | None = Field(
        description="The effective-dated org-wide ceiling in micro-USD; null "
        "only when ceiling_configured is false."
    )
    remaining_micros: int | None = Field(
        description="ceiling_micros - period_to_date_micros; may be negative "
        "(the gate is a soft cap with bounded overshoot). Null only when "
        "ceiling_configured is false."
    )
    detail: str | None = Field(
        description="Human-legible explanation when no ceiling is configured; "
        "null otherwise."
    )


@router.get("/position", response_model=SpendPositionResponse)
async def get_spend_position(
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin")),
    container: Container = Depends(get_container),
) -> SpendPositionResponse:
    if container.cost_ledger is None or container.spend_ceiling_dal is None:
        raise HTTPException(
            status_code=503,
            detail="spend position requires the postgres backend",
        )
    # Same clock + period convention as the enforcement path
    # (SpendCeilingEnforcer.enforce and the ledger's billing_period stamp).
    billing_period = datetime.now(timezone.utc).strftime("%Y-%m")
    ceiling_micros = await container.spend_ceiling_dal.read_ceiling_micros(
        ctx.org_id, billing_period
    )
    period_to_date = await container.cost_ledger.org_period_total_micros(
        ctx.org_id, billing_period
    )
    if ceiling_micros is None:
        return SpendPositionResponse(
            billing_period=billing_period,
            period_to_date_micros=period_to_date,
            ceiling_configured=False,
            ceiling_micros=None,
            remaining_micros=None,
            detail=(
                f"no org spend ceiling configured for period {billing_period!r}; "
                "every LLM call is refused (fail closed, D6) until an operator "
                "sets a ceiling"
            ),
        )
    return SpendPositionResponse(
        billing_period=billing_period,
        period_to_date_micros=period_to_date,
        ceiling_configured=True,
        ceiling_micros=ceiling_micros,
        remaining_micros=ceiling_micros - period_to_date,
        detail=None,
    )
