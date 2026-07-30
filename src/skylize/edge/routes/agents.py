"""Agent execution routes.

POST /api/v1/agents/execute  — run an agent, get back a deliverable_id
GET  /api/v1/agents          — list available agents with input schemas
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ...bootstrap import Container
from ...schemas.base import RequestContext
from ..deps import enforce_rate_limit, get_container, require_any_role_or_user
from ..errors import CodedHTTPException, ErrorCode

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ExecuteAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(min_length=1, max_length=200)
    input: dict[str, Any] = Field(default_factory=dict)


class ExecuteAgentResponse(BaseModel):
    deliverable_id: UUID
    status: str
    agent_id: str
    title: str


class AgentInfo(BaseModel):
    agent_id: str
    name: str
    description: str
    department: str
    authority_level: str
    input_schema: dict[str, Any]


class AgentListResponse(BaseModel):
    agents: list[AgentInfo]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post(
    "/execute",
    response_model=ExecuteAgentResponse,
    status_code=201,
    # The most expensive route in the product: it calls a paid provider. The
    # limiter is per-org and IN-PROCESS (edge/rate_limit.py:16), so the
    # effective limit is the configured value times the worker/replica count.
    dependencies=[Depends(enforce_rate_limit)],
)
async def execute_agent(
    body: ExecuteAgentRequest,
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin", "operator")),
    container: Container = Depends(get_container),
) -> ExecuteAgentResponse | JSONResponse:
    from ...adapters.llm.gateway import (
        LLMMalformedResponse,
        LLMModelNotPriced,
        LLMProviderUnavailable,
        LLMTimeout,
        TokenBudgetExceeded,
    )
    from ...adapters.llm.spend_ceiling import OrgSpendCeilingExceeded
    from ...app.agents.execution import (
        AgentDeferredToHuman,
        AgentGovernanceRejected,
        AgentInputError,
        AgentOutputError,
    )
    from ...app.governance import GovernanceDenied
    from ...contracts.registry import AgentNotRegistered

    try:
        row = await container.agent_execution.execute(
            org_id=ctx.org_id,
            agent_id=body.agent_id,
            input_data=body.input,
            user_id=ctx.user_id,
        )
    except AgentNotRegistered as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AgentInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AgentGovernanceRejected as exc:
        # Decision engine rejected the request (owner decision D4): 403 carrying
        # the decision reason. No LLM call, no deliverable, no ledger row happened.
        # Status and detail are unchanged; `code` is what lets a client tell this
        # apart from the other two 403s this route can produce.
        raise CodedHTTPException(
            status_code=403,
            detail=f"decision rejected: {exc}",
            code=ErrorCode.DECISION_REJECTED,
        ) from exc
    except AgentDeferredToHuman as exc:
        # Decision engine deferred to a human (owner decision D4): 202 carrying the
        # hitl_id of the queued escalation. No LLM call, no deliverable, no ledger
        # row happened; the hitl_queue row was written before this response.
        return JSONResponse(
            status_code=202,
            content={
                "hitl_id": str(exc.hitl_id),
                "status": "deferred_to_human",
                "agent_id": body.agent_id,
                "reason": str(exc),
            },
        )
    except GovernanceDenied as exc:
        # Kill switch / suspension: the denial is already audited by the
        # authority — surface it as forbidden, not a 500. The detail carries the
        # authority's own reason, which names WHICH control fired (platform /
        # tenant / agent kill switch, or agent suspension — snapshot.py:53-63).
        raise CodedHTTPException(
            status_code=403,
            detail=f"governance denied: {exc}",
            code=ErrorCode.GOVERNANCE_DENIED,
        ) from exc
    except TokenBudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=f"token budget exceeded: {exc}") from exc
    except OrgSpendCeilingExceeded as exc:
        # A GOVERNED refusal, decided before any provider egress: nothing was
        # sent and nothing was charged. 403 matches the other governance
        # refusals on this route; the code is what tells them apart.
        #
        # Every figure below is the CALLER'S OWN: the enforcer is invoked with
        # request.org_id, which is ctx.org_id from the authenticated context, so
        # no other tenant's ceiling or spend can appear here. org_id itself is
        # deliberately NOT echoed (the caller already knows it; it stays in the
        # audit record and the log).
        if exc.ceiling_micros is None:
            # No ceiling row for (org, period). period_to_date is None here and
            # means NOT READ, not zero — rendering it as 0 would assert the org
            # has spent nothing, which may be false. So it is omitted entirely.
            raise CodedHTTPException(
                status_code=403,
                detail=(
                    "no spend ceiling is configured for billing period "
                    f"{exc.billing_period}; calls are refused until an operator "
                    "sets one. No model was called and nothing was charged."
                ),
                code=ErrorCode.SPEND_CEILING_NOT_CONFIGURED,
            ) from exc
        raise CodedHTTPException(
            status_code=403,
            detail=(
                f"spend ceiling reached for billing period {exc.billing_period}: "
                f"ceiling {exc.ceiling_micros} micro-USD, "
                f"spent so far {exc.period_to_date_micros} micro-USD, "
                f"this call estimated at {exc.estimated_micros} micro-USD. "
                "No model was called and nothing was charged."
            ),
            code=ErrorCode.SPEND_CEILING_EXCEEDED,
        ) from exc
    except LLMModelNotPriced as exc:
        # Server-side provisioning fault: this deployment cannot price the model
        # the agent would call, so it refuses rather than producing a charge it
        # could not record. 503 matches how the codebase already reports an
        # unprovisioned capability (spend.py, knowledge.py). The concrete model
        # id is NOT echoed to the caller — it is our configuration, not theirs —
        # and stays in the chained exception for the log.
        raise CodedHTTPException(
            status_code=503,
            detail=(
                "this deployment has no price configured for the model backing "
                "this agent, so the call was refused before it was sent. No "
                "model was called and nothing was charged."
            ),
            code=ErrorCode.MODEL_NOT_PRICED,
        ) from exc
    except LLMTimeout as exc:
        # 504 is exactly this: an upstream did not answer in time. NOT retried
        # anywhere — a timed-out call may have completed and been billed.
        raise CodedHTTPException(
            status_code=504,
            detail="the model provider did not respond within the configured timeout",
            code=ErrorCode.PROVIDER_TIMEOUT,
        ) from exc
    except (LLMProviderUnavailable, LLMMalformedResponse) as exc:
        # 502 is exactly this: an invalid or absent response from an upstream.
        # Both causes share the status and the code because the caller's position
        # is identical — the provider failed us, there is nothing to fix here.
        raise CodedHTTPException(
            status_code=502,
            detail="the model provider is unavailable or returned an unusable response",
            code=ErrorCode.PROVIDER_UNAVAILABLE,
        ) from exc
    except AgentOutputError as exc:
        raise HTTPException(status_code=502, detail=f"LLM output error: {exc}") from exc

    # Approve (owner decision D4): 201 shape byte-identical to today.
    return ExecuteAgentResponse(
        deliverable_id=row.id,
        status=row.status,
        agent_id=row.agent_id,
        title=row.title,
    )


@router.get("", response_model=AgentListResponse)
async def list_agents(
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin", "operator", "analyst", "viewer")),
    container: Container = Depends(get_container),
) -> AgentListResponse:
    agents_data = container.agent_execution.list_agents()
    agents = [
        AgentInfo(
            agent_id=a["agent_id"],
            name=a["name"],
            description=a["description"],
            department=a["department"],
            authority_level=a["authority_level"],
            input_schema=a["input_schema"],
        )
        for a in agents_data
    ]
    return AgentListResponse(agents=agents)
