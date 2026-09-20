"""Workflow routes — the worked creative path, plus its catalogue and history.

THREE endpoints, and an honesty note that governs two of them.

  * ``POST /creative``  — trigger. Unchanged.
  * ``GET  ""``         — the workflow CATALOGUE. It has ONE entry, because the
    system can run one workflow. ``Orchestrator.__init__`` builds exactly one
    graph, ``build_creative_graph``, and ``POST /creative`` is the only route
    that reaches it. The entry is DERIVED — the agent id comes from the
    orchestrator's own constant and the human-facing fields from that agent's
    registered contract — so it cannot drift from what actually runs, and it
    cannot be padded. A catalogue with five plausible-looking workflows would
    be a list of things the console can offer and the backend cannot do.
  * ``GET  /runs``      — real run history from ``workflow_runs``
    (migration 0033), written best-effort by ``Orchestrator.invoke``.

NO STAGE PROGRESS EXISTS. Neither endpoint returns per-stage advancement and
neither can: the live LangGraph emits no per-node persistence, so nothing
records that a run reached node 2 of 4. ``failure_stage`` on a run says where a
run STOPPED, which is not the same claim. A UI pipeline rendering stages as
"done / in progress / pending" from this data would be inventing every one of
those states. ``stages`` in the catalogue is therefore the graph's STATIC node
sequence — the shape of the workflow, not the position of any run in it.

RBAC: both reads are ``require_any_role_or_user("owner", "admin")``, the same
gate every other console read uses (audit.py, autonomy.py). The trigger keeps
its existing ``enforce_rate_limit`` context — this change does not loosen or
tighten who may run a workflow.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ...app.orchestrator.orchestrator import CREATIVE_WORKFLOW_NAME
from ...bootstrap import Container
from ...contracts.registry import AgentNotRegistered
from ...schemas.base import RequestContext
from ..deps import (
    enforce_rate_limit,
    get_container,
    require_any_role_or_user,
)

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])

#: The agent `POST /creative` invokes. Single source shared with the handler
#: below so the catalogue cannot advertise an agent the trigger does not call.
_CREATIVE_AGENT_ID = "hook_generator_agent"

#: The STATIC node sequence of `build_creative_graph`
#: (app/orchestrator/workflows/creative_workflow.py): a governance checkpoint,
#: the agent step, an emit node. This is the workflow's SHAPE. It is not, and
#: must not be rendered as, a run's progress — no node records being reached.
_CREATIVE_STAGES: tuple[str, ...] = ("governance_checkpoint", "agent_step", "emit")


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


class WorkflowDefinitionResponse(BaseModel):
    """One workflow the system can ACTUALLY run.

    Every field is derived from the orchestrator's graph or the agent's
    registered contract. Nothing here is authored for display.
    """

    name: str = Field(description="Stable workflow key, used in run rows.")
    agent_id: str
    agent_role: str = Field(description="From the registered AgentContract.")
    department: str
    authority_level: str
    trigger_path: str = Field(
        description="The route that runs this workflow. The catalogue lists "
        "nothing that has no trigger."
    )
    stages: list[str] = Field(
        description="The graph's STATIC node sequence — the workflow's shape. "
        "NOT per-run progress: the live path records no stage advancement, so "
        "no run can be said to be at any of these."
    )


class WorkflowDefinitionListResponse(BaseModel):
    workflows: list[WorkflowDefinitionResponse]
    stage_progress_supported: bool = Field(
        default=False,
        description="Always false today, stated explicitly so a client cannot "
        "infer stage tracking from the presence of `stages`. The live "
        "orchestrator persists no per-node progress.",
    )


class WorkflowRunResponse(BaseModel):
    run_id: UUID
    workflow_name: str
    agent_id: str
    status: str = Field(description="running | completed | denied | failed")
    correlation_id: UUID = Field(
        description="Joins this run to its audit rows and its cost rows."
    )
    started_at: datetime
    finished_at: datetime | None
    failure_stage: str | None = Field(
        default=None,
        description="Where the run STOPPED, when it did not complete. Not a "
        "progress marker — a completed run has none, and a failed one reached "
        "no recorded milestone before this point.",
    )
    reason: str | None


class WorkflowRunListResponse(BaseModel):
    runs: list[WorkflowRunResponse]
    # Keyset cursor, the shape the audit feed uses: pass as `before` for the
    # next (older) page. None = no more.
    next_before: datetime | None


@router.get("", response_model=WorkflowDefinitionListResponse)
async def list_workflows(
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin")),
    container: Container = Depends(get_container),
) -> WorkflowDefinitionListResponse:
    """The workflows this deployment can run. Today: exactly one.

    The list is built from the one graph `Orchestrator` constructs, resolved
    against the live registry. If the creative agent is not registered in this
    deployment the list is EMPTY rather than one entry describing an agent that
    cannot be invoked — the same fail-closed instinct the orchestrator applies
    at its own resolve step.
    """
    try:
        contract = container.orchestrator.registry.resolve(_CREATIVE_AGENT_ID)
    except AgentNotRegistered:
        return WorkflowDefinitionListResponse(workflows=[])
    return WorkflowDefinitionListResponse(
        workflows=[
            WorkflowDefinitionResponse(
                name=CREATIVE_WORKFLOW_NAME,
                agent_id=contract.agent_id,
                agent_role=contract.agent_role,
                department=contract.department,
                authority_level=contract.authority_level,
                trigger_path="/api/v1/workflows/creative",
                stages=list(_CREATIVE_STAGES),
            )
        ]
    )


@router.get("/runs", response_model=WorkflowRunListResponse)
async def list_workflow_runs(
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = None,
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin")),
    container: Container = Depends(get_container),
) -> WorkflowRunListResponse:
    """Real run history for this org, newest first.

    503 on the memory backend rather than an empty list: "no durable store" and
    "no runs yet" are different facts, and an empty page would let a console
    report the second while the first is true.
    """
    if before is not None and before.tzinfo is None:
        raise HTTPException(
            status_code=422, detail="before must be timezone-aware (ISO 8601)"
        )
    dal = container.workflow_runs_dal
    if dal is None:
        raise HTTPException(
            status_code=503, detail="workflow run history requires the postgres backend"
        )
    rows = await dal.list_runs(ctx.org_id, limit=limit, before=before)
    runs = [
        WorkflowRunResponse(
            run_id=r.run_id,
            workflow_name=r.workflow_name,
            agent_id=r.agent_id,
            status=r.status,
            correlation_id=r.correlation_id,
            started_at=r.started_at,
            finished_at=r.finished_at,
            failure_stage=r.failure_stage,
            reason=r.reason,
        )
        for r in rows
    ]
    return WorkflowRunListResponse(
        runs=runs,
        next_before=runs[-1].started_at if len(runs) == limit else None,
    )


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
        _CREATIVE_AGENT_ID, payload, org_id=ctx.org_id
    )
    return WorkflowResponse(
        status=result.status, agent_id=result.agent_id,
        correlation_id=result.correlation_id, token_id=result.token_id,
        event_type=result.event_type, output=result.output, reason=result.reason,
    )
