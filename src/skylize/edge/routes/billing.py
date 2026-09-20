"""Billing usage read route — REAL ai_cost_ledger spend, for the console.

Read-only, and deliberately NARROW. This endpoint answers exactly one question:
*what has this org actually spent on LLM tokens, and against what ceiling?* Every
number it returns is aggregated from rows that exist — ``ai_cost_ledger``
(migration 0012) and ``org_spend_ceiling`` (migration 0014).

WHAT THIS ROUTE DELIBERATELY DOES NOT RETURN, and why
-----------------------------------------------------
The console's Billing screen also has sections for a plan tier, invoices, seat
counts and agent-slot caps. NONE of those have a backing table, a migration, or a
single row anywhere in this repo. This response therefore has NO field for any of
them -- not a null, not a zero, not an empty list. A null would read as "we have
this concept and it happens to be unset"; the truth is that the concept does not
exist yet. Their STRUCTURAL ABSENCE is the honest signal, and
``unavailable_sections`` names them explicitly so the UI can say "not available
yet" from data rather than from a hard-coded string.

The one commercial-looking number here, ``ceiling_micros``, is NOT a plan
allowance: ``org_spend_ceiling`` is a GOVERNANCE cap (migration 0014, owner
decision D1) set by ops, read by the pre-call spend gate. It is honest to show
"spent X of ceiling Y"; it would not be honest to call Y a plan.

UNITS (ADR-0006 -- the three ledgers must never be conflated)
-------------------------------------------------------------
Every money field on this response is named ``*_micros`` and is MICRO-currency:
millionths of one currency unit, the unit ``ai_cost_ledger.cost_micros`` and
``org_spend_ceiling.ceiling_micros`` both store. It is NOT cents / minor units --
the unit ``budget_ledger`` uses -- and conflating them is a 10,000x error. No
field here is ever a bare ``cost``. Conversion to cents happens ONCE, at a
display boundary, via ``dal.cost_ledger.micros_to_minor`` -- never in this route
and never per row.

ZERO IS A REAL ANSWER. ``model_pricing`` ships EMPTY by design (migration 0012
§"Seed"), so an environment where ops has not seeded provider prices records no
ledger rows and this endpoint truthfully reports 0 spend and an empty breakdown.
That is the correct output, not a bug and not a reason to fabricate a number.

Conventions mirror the spend position route (edge/routes/spend.py), which reads
the same two sources for the narrower "am I over the ceiling" question: typed
Pydantic models, ``org_id`` strictly from the authenticated ``RequestContext``
and never from a query parameter or body field, RLS-scoped DALs underneath, and
``require_any_role_or_user("owner", "admin")`` for the read. There is no write
verb: the ceiling is set only through the audited
``OrgSpendCeilingDAL.set_ceiling`` operator seam.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ...bootstrap import Container
from ...schemas.base import RequestContext
from ..deps import get_container, require_any_role_or_user

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])

# The Billing screen's sections that have NO backend whatsoever. Named here so
# the console renders "not available yet" from the API rather than from a string
# baked into the bundle, and so adding one later is a visible API change.
UNAVAILABLE_SECTIONS: tuple[str, ...] = (
    "plan_tier",
    "invoices",
    "seats",
    "agent_slots",
)

# Bound on the history window (calendar months). 12 keeps a year of usage on one
# page load; the ledger could hold far more.
_DEFAULT_HISTORY_PERIODS = 12
_MAX_HISTORY_PERIODS = 36


class ModelUsage(BaseModel):
    """One (provider, model) pair's share of the CURRENT period's spend."""

    provider: str = Field(description="Provider key as recorded on the ledger row.")
    model: str = Field(description="Concrete provider model id, as billed.")
    cost_micros: int = Field(
        description="Micro-currency (millionths of one unit of `currency`) -- "
        "NOT cents. Charges net of reversals."
    )
    input_tokens: int = Field(description="Raw provider input tokens, net of reversals.")
    output_tokens: int = Field(
        description="Raw provider output tokens, net of reversals."
    )
    currency: str = Field(
        description="Currency of `cost_micros`, read from the ledger rows -- never "
        "assumed. Rows in different currencies are reported as separate entries."
    )


class PeriodUsage(BaseModel):
    """One calendar month's org-wide totals."""

    billing_period: str = Field(
        description="Calendar month as '%Y-%m', e.g. '2026-07' -- the same period "
        "convention the LLM egress gate and the cost ledger use."
    )
    cost_micros: int = Field(
        description="Org-wide spend for that month in micro-currency, across all "
        "providers, charges net of reversals. NOT cents."
    )
    input_tokens: int = Field(description="Org-wide input tokens for that month.")
    output_tokens: int = Field(description="Org-wide output tokens for that month.")


class BillingUsageResponse(BaseModel):
    """Real, ledger-backed usage. Contains no plan, invoice, seat or slot data.

    Their absence is STRUCTURAL -- there is no field to be null -- and
    `unavailable_sections` names them so the console can say so out loud.
    """

    billing_period: str = Field(
        description="The CURRENT calendar month (UTC) the figures below describe."
    )
    current_period: PeriodUsage = Field(
        description="This month's org-wide totals. All-zero is a REAL answer: an "
        "org that has spent nothing, or an environment where model_pricing is "
        "unseeded (it ships empty by design), genuinely has zero recorded cost."
    )
    models: list[ModelUsage] = Field(
        description="This month's spend broken down by provider + model, dearest "
        "first. Empty when the ledger holds no rows for this org this month."
    )
    history: list[PeriodUsage] = Field(
        description="Preceding calendar months, newest first, including the "
        "current one. Bounded by `periods`."
    )
    ceiling_configured: bool = Field(
        description="False when no org_spend_ceiling row resolves at or before "
        "this period. The LLM egress gate then fails closed (owner decision D6): "
        "every call is refused until an operator sets a ceiling."
    )
    ceiling_micros: int | None = Field(
        description="The effective-dated ORG SPEND CEILING in micro-currency. This "
        "is a GOVERNANCE cap set by ops (migration 0014), NOT a commercial plan "
        "allowance. Null only when `ceiling_configured` is false."
    )
    remaining_micros: int | None = Field(
        description="ceiling_micros - current_period.cost_micros; MAY BE NEGATIVE "
        "(the gate is a soft cap with bounded overshoot). Null only when "
        "`ceiling_configured` is false."
    )
    unavailable_sections: list[str] = Field(
        description="Billing-screen sections with NO backend in this deployment. "
        "The console must render each as explicitly unavailable and must not "
        "substitute a placeholder value. Currently: plan tier, invoices, seats, "
        "agent slots."
    )
    detail: str | None = Field(
        description="Human-legible explanation when no ceiling is configured; "
        "null otherwise."
    )


@router.get("/usage", response_model=BillingUsageResponse)
async def get_billing_usage(
    periods: int = Query(
        _DEFAULT_HISTORY_PERIODS,
        ge=1,
        le=_MAX_HISTORY_PERIODS,
        description="How many calendar months of history to return, newest first.",
    ),
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin")),
    container: Container = Depends(get_container),
) -> BillingUsageResponse:
    if container.cost_ledger is None or container.spend_ceiling_dal is None:
        raise HTTPException(
            status_code=503,
            detail="billing usage requires the postgres backend",
        )
    # Same clock + period convention as the enforcement path and edge/routes/
    # spend.py, so the console cannot show one month here and another there.
    billing_period = datetime.now(timezone.utc).strftime("%Y-%m")

    history = await container.cost_ledger.org_period_history(
        ctx.org_id, limit=periods
    )
    models = await container.cost_ledger.org_period_model_breakdown(
        ctx.org_id, billing_period
    )
    ceiling_micros = await container.spend_ceiling_dal.read_ceiling_micros(
        ctx.org_id, billing_period
    )

    # The current month may be absent from `history` entirely (an org that has
    # spent nothing this month has no rows to group). Synthesise the zero row
    # rather than omitting the field: "you have spent nothing" is a real answer
    # the screen must be able to render, and it is NOT fabricated data -- it is
    # the true SUM over an empty set.
    current = next(
        (p for p in history if p.billing_period == billing_period), None
    )
    current_period = PeriodUsage(
        billing_period=billing_period,
        cost_micros=current.cost_micros if current else 0,
        input_tokens=current.input_tokens if current else 0,
        output_tokens=current.output_tokens if current else 0,
    )

    return BillingUsageResponse(
        billing_period=billing_period,
        current_period=current_period,
        models=[
            ModelUsage(
                provider=m.provider,
                model=m.model,
                cost_micros=m.cost_micros,
                input_tokens=m.input_tokens,
                output_tokens=m.output_tokens,
                currency=m.currency,
            )
            for m in models
        ],
        history=[
            PeriodUsage(
                billing_period=p.billing_period,
                cost_micros=p.cost_micros,
                input_tokens=p.input_tokens,
                output_tokens=p.output_tokens,
            )
            for p in history
        ],
        ceiling_configured=ceiling_micros is not None,
        ceiling_micros=ceiling_micros,
        remaining_micros=(
            None
            if ceiling_micros is None
            else ceiling_micros - current_period.cost_micros
        ),
        unavailable_sections=list(UNAVAILABLE_SECTIONS),
        detail=(
            None
            if ceiling_micros is not None
            else (
                f"no org spend ceiling configured for period {billing_period!r}; "
                "every LLM call is refused (fail closed, D6) until an operator "
                "sets a ceiling"
            )
        ),
    )
