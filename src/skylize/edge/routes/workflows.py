"""Workflow trigger routes — the worked creative path."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ...bootstrap import Container
from ...schemas.base import RequestContext
from ..deps import enforce_rate_limit, get_container

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


class CreativeRunRequest(BaseModel):
    """Public request shape — kept stable for the console BFF.

    `brief_id` is accepted for back-compat but the operator-execute contract
    (HookGeneratorExecuteIn) is briefless; the run's correlation_id serves as
    the brief surrogate downstream.
    """

    model_config = ConfigDict(extra="forbid")
    brief_id: UUID | None = None
    brand_name: str | None = None
    product: str
    audience: str
    tone: str | None = None
    count: int = Field(default=3, ge=1, le=10)


class WorkflowResponse(BaseModel):
    status: str
    agent_id: str
    correlation_id: UUID
    token_id: UUID | None = None
    event_type: str | None = None
    output: dict[str, object] | None = None
    reason: str | None = None


@router.post("/creative", response_model=WorkflowResponse)
async def run_creative(
    body: CreativeRunRequest,
    ctx: RequestContext = Depends(enforce_rate_limit),
    container: Container = Depends(get_container),
) -> WorkflowResponse:
    payload = {
        "brand_name": body.brand_name or body.product,
        "product_description": body.product,
        "target_audience": body.audience,
        "tone": body.tone,
        "count": body.count,
    }
    result = await container.orchestrator.invoke(
        "hook_generator_agent", payload, org_id=ctx.org_id
    )
    return WorkflowResponse(
        status=result.status, agent_id=result.agent_id,
        correlation_id=result.correlation_id, token_id=result.token_id,
        event_type=result.event_type, output=result.output, reason=result.reason,
    )
