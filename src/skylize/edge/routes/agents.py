"""Agent execution routes.

POST /api/v1/agents/execute  — run an agent, get back a deliverable_id
GET  /api/v1/agents          — list available agents with input schemas
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ...bootstrap import Container
from ...schemas.base import RequestContext
from ..deps import get_container, require_any_role_or_user

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

@router.post("/execute", response_model=ExecuteAgentResponse, status_code=201)
async def execute_agent(
    body: ExecuteAgentRequest,
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin", "operator")),
    container: Container = Depends(get_container),
) -> ExecuteAgentResponse:
    from ...adapters.llm.gateway import TokenBudgetExceeded
    from ...app.agents.execution import AgentInputError, AgentOutputError
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
    except GovernanceDenied as exc:
        # Kill switch / suspension: the denial is already audited by the
        # authority — surface it as forbidden, not a 500.
        raise HTTPException(status_code=403, detail=f"governance denied: {exc}") from exc
    except TokenBudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=f"token budget exceeded: {exc}") from exc
    except AgentOutputError as exc:
        raise HTTPException(status_code=502, detail=f"LLM output error: {exc}") from exc

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
