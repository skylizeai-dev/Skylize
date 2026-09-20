"""Models route — the REAL model catalogue and this org's routing rules.

ONE GET, read-only. There is no PUT here: writing routing changes which priced
model an org's spend is incurred against, and that setter (``set_rules``) exists
in the DAL but is not yet exposed. Shipping the read first keeps the console
honest about what the platform actually routes before giving it a lever.

Conventions follow the autonomy route (edge/routes/autonomy.py): typed Pydantic
models, org_id strictly from the authenticated RequestContext and never from a
query parameter, and an RLS-scoped DAL underneath. RBAC is
``require_any_role_or_user("owner", "admin")``, the same read role every other
console read uses.

WHAT THIS ROUTE REPORTS, AND WHY IT REPORTS NOTHING ELSE.

The catalogue is the REAL logical -> concrete map: three logical names
("default", "fast", "reasoning") resolved from Settings
(config.py:311-313) -- the exact same map the Anthropic adapter builds at
construction (adapters/llm/anthropic_adapter.py:267-271) and enforces at egress
(``_concrete_model``, anthropic_adapter.py:368-374). A console rendering this
shows the model id a request would actually reach.

`pricing` is present ONLY when ``model_pricing`` (migration 0012) has a row
covering that concrete model. That table is seeded EMPTY by design, so on an
unseeded deployment every entry comes back ``pricing: null``. Null means
"nobody has priced this model" -- a true statement about the deployment. A
zero, or a number copied from a vendor page, would be a claim the backend
cannot support.

DELIBERATELY ABSENT, because no backend source exists for any of them:
  * LATENCY -- nothing measures or stores per-model latency. Not in
    ai_cost_ledger (migration 0012 records tokens, cost and timestamps, never
    duration), not in the adapter, not in any table.
  * CONTEXT WINDOW -- a provider fact nothing in this repo records. Settings
    holds the model ID string and nothing else about the model.
  * TRAFFIC SHARE -- not a configured value. It is DERIVABLE from
    ai_cost_ledger, which holds one immutable row per real provider call with
    its org_id and concrete model, so the honest version of this number is an
    aggregate query over what actually ran. It is not served here, and it is
    emphatically not a column on model_routing_rules.

Adding a field for any of the three would mean typing a number into the
response that nothing measured.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...bootstrap import Container
from ...dal.model_routing import ModelRoutingDAL
from ...schemas.base import RequestContext
from ..deps import get_container, require_any_role_or_user

router = APIRouter(prefix="/api/v1/models", tags=["models"])


class ModelPricingResponse(BaseModel):
    """An actual model_pricing row, in migration 0012's own units.

    Micro-currency per 1,000,000 tokens, so every real quoted price is an exact
    integer and no float ever touches money ($3.00/Mtok == 3_000_000). The
    console formats; it does not receive a pre-rounded number.
    """

    input_price_micros_per_mtok: int = Field(
        description="Micro-currency per 1e6 input tokens, straight from model_pricing."
    )
    output_price_micros_per_mtok: int = Field(
        description="Micro-currency per 1e6 output tokens, straight from model_pricing."
    )
    currency: str
    pricing_version: int = Field(
        description="The model_pricing row's version, so a displayed price is "
        "traceable to the exact price line it came from."
    )


class ModelCatalogueEntry(BaseModel):
    logical_name: str = Field(
        description="The logical model an agent names on a gateway request. One "
        "of 'default', 'fast', 'reasoning' — the keys of the adapter's own map."
    )
    concrete_model: str = Field(
        description="The concrete provider model id this logical name resolves "
        "to, from Settings. This is the id a real request reaches."
    )
    provider: str = Field(description="The provider that serves the concrete model.")
    pricing: ModelPricingResponse | None = Field(
        default=None,
        description="The active model_pricing row, or NULL when this deployment "
        "has not priced the model. model_pricing is seeded empty by design, so "
        "null is the expected value until ops seeds it. Never a fabricated "
        "number and never a zero standing in for one.",
    )


class ModelRoutingRuleResponse(BaseModel):
    routing_class: str = Field(
        description="The logical class an agent names. Always all three classes "
        "are returned, so an absent rule never has to be inferred."
    )
    target_logical_model: str = Field(
        description="The logical model the class routes to. Equal to "
        "`routing_class` when the org has configured nothing — the identity "
        "mapping, which is exactly what the adapter does today."
    )
    fallback_logical_model: str | None = Field(
        default=None,
        description="The class to try when the target is refused. NULL means no "
        "fallback: refuse rather than spend on a model the org did not choose.",
    )
    configured: bool = Field(
        description="False when no row exists and this is the fail-closed "
        "identity mapping rather than a choice. Display only."
    )


class ModelsResponse(BaseModel):
    """The whole Models screen's real data, and nothing it cannot source.

    There is no `latency_ms`, no `context_window` and no `traffic_share_pct`
    here. See this module's docstring: no backend source exists for the first
    two, and the third is derivable from ai_cost_ledger rather than configured.
    """

    catalogue: list[ModelCatalogueEntry]
    routing: list[ModelRoutingRuleResponse]
    pricing_configured: bool = Field(
        description="True when at least one catalogue entry carries a price. "
        "False tells the console to say 'not priced on this deployment' out "
        "loud instead of rendering an empty cost column that looks like zero."
    )


def _require_dal(container: Container) -> "ModelRoutingDAL":
    if container.model_routing_dal is None:
        raise HTTPException(
            status_code=503,
            detail="model routing requires the postgres backend",
        )
    return container.model_routing_dal


@router.get("", response_model=ModelsResponse)
async def get_models(
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin")),
    container: Container = Depends(get_container),
) -> ModelsResponse:
    dal = _require_dal(container)
    catalogue = await dal.read_catalogue(ctx.org_id, container.settings)
    routing = await dal.read_rules(ctx.org_id)
    entries = [
        ModelCatalogueEntry(
            logical_name=entry.logical_name,
            concrete_model=entry.concrete_model,
            provider=entry.provider,
            pricing=(
                None
                if entry.pricing is None
                else ModelPricingResponse(
                    input_price_micros_per_mtok=entry.pricing.input_price_micros_per_mtok,
                    output_price_micros_per_mtok=entry.pricing.output_price_micros_per_mtok,
                    currency=entry.pricing.currency,
                    pricing_version=entry.pricing.pricing_version,
                )
            ),
        )
        for entry in catalogue
    ]
    return ModelsResponse(
        catalogue=entries,
        routing=[
            ModelRoutingRuleResponse(
                routing_class=rule.routing_class,
                target_logical_model=rule.target_logical_model,
                fallback_logical_model=rule.fallback_logical_model,
                configured=rule.configured,
            )
            for rule in routing
        ],
        pricing_configured=any(entry.pricing is not None for entry in entries),
    )
