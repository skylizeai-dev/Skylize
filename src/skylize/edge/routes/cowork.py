"""The co-work chat surface — POST /api/v1/cowork/turns.

ONE TURN IS ONE `AgentExecutionService.execute()` CALL, AND THERE IS NO OTHER
PATH FROM THIS ROUTE TO A TOOL. That is the whole design, and it is worth being
explicit about what it rules out: this handler does not hold a `ToolProxy`, does
not hold an `LLMGateway`, does not construct a `GovernanceToken`, and does not
import `skylize.tools` at all. It has exactly one collaborator that can reach a
tool -- `container.agent_execution` -- so a chat turn walks the identical
governed pipeline that `POST /api/v1/agents/execute` walks: the synchronous
decision gate (step 2.5), the mint, the ordered token validation before every LLM
egress, and `ToolProxy.invoke`'s own `validate_tool_call` on every tool call.
There is no "it's just chat" fast path, and none can be added here without
importing something this module deliberately does not.

WHAT MAKES IT THE PER-EMPLOYEE SHAPE. The one argument this route adds over
`/agents/execute` is `on_behalf_of_principal=ctx.user_id`. That flips `mint` to
the v1.1 token shape, which intersects the contract's manifest with the caller's
OWN compiled authority before signing (app/principal/authority.py:112). So the
session can never do anything the employee could not do, and an employee who
lacks a tool simply does not get it in the token -- the denial then lands at
`ValidationStage.SCOPE` (contracts/token.py:400-410), not here.

`principal_id` is `ctx.user_id` and never a body field. `RequestContext.user_id`
is the JWT `sub` (edge/deps.py:75), minted from `users.user_id`
(edge/routes/auth.py:136), which is exactly the derivation migration 0020 uses
for `principal.principal_id`. Same convention as brief.py, and for the same
reason: a caller must never be able to name someone else as the principal.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from ...app.principal.models import ActorKind
from ...bootstrap import Container
from ...schemas.agents.cowork import CoworkTurnIn
from ...schemas.base import RequestContext
from ..deps import enforce_rate_limit, get_container, require_any_role_or_user
from ..errors import CodedHTTPException, ErrorCode

router = APIRouter(prefix="/api/v1/cowork", tags=["cowork"])

#: The agent this surface talks to. A constant, never a request field: letting a
#: caller name the agent would turn this into a second, less-governed
#: `/agents/execute` whose contract nobody could predict from the route.
COWORK_AGENT_ID = "cowork_agent"

_ROLES = ("owner", "admin", "operator")


class CoworkTurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The agent's reply, as it was PERSISTED and audited -- the deliverable's own
    #: rendered body, not a second string derived separately. Returning the stored
    #: artefact means what the employee reads is exactly what the audit trail
    #: holds; deriving a parallel reply would let the two drift.
    reply: str
    deliverable_id: UUID
    agent_id: str


@router.post(
    "/turns",
    response_model=CoworkTurnResponse,
    status_code=201,
    # Same limiter as /agents/execute: a turn calls a paid provider, and a chat
    # surface invites far more of them than a batch endpoint does.
    dependencies=[Depends(enforce_rate_limit)],
)
async def cowork_turn(
    body: CoworkTurnIn,
    ctx: RequestContext = Depends(require_any_role_or_user(*_ROLES)),
    container: Container = Depends(get_container),
) -> CoworkTurnResponse | JSONResponse:
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
    from ...app.principal.errors import PrincipalError

    try:
        row = await container.agent_execution.execute(
            org_id=ctx.org_id,
            agent_id=COWORK_AGENT_ID,
            input_data=body.model_dump(mode="json"),
            user_id=ctx.user_id,
            # The one line that makes this the per-employee shape.
            on_behalf_of_principal=ctx.user_id,
        )
    except AgentInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PrincipalError as exc:
        # The caller's ROLE let them in; their own authority did not. Distinct
        # from the other three 403s: not a decision verdict, not a platform
        # control, not a missing role. `failed_stage` names which check refused
        # (app/principal/errors.py) so the console can be specific.
        raise CodedHTTPException(
            status_code=403,
            detail=f"principal authority denied [{exc.failed_stage}]: {exc}",
            code=ErrorCode.PRINCIPAL_AUTHORITY_DENIED,
        ) from exc
    except AgentGovernanceRejected as exc:
        raise CodedHTTPException(
            status_code=403,
            detail=f"decision rejected: {exc}",
            code=ErrorCode.DECISION_REJECTED,
        ) from exc
    except AgentDeferredToHuman as exc:
        # Same 202 contract as /agents/execute: the hitl_queue row is durable
        # before this response (app/agents/execution.py `_govern` ordering, D3).
        # With `defers_on_trigger_presence=False` an ORDINARY turn no longer
        # lands here -- only a genuinely gated condition does.
        return JSONResponse(
            status_code=202,
            content={
                "hitl_id": str(exc.hitl_id),
                "status": "deferred_to_human",
                "agent_id": COWORK_AGENT_ID,
                "reason": str(exc),
            },
        )
    except GovernanceDenied as exc:
        raise CodedHTTPException(
            status_code=403,
            detail=f"governance denied: {exc}",
            code=ErrorCode.GOVERNANCE_DENIED,
        ) from exc
    except TokenBudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=f"token budget exceeded: {exc}") from exc
    except OrgSpendCeilingExceeded as exc:
        raise CodedHTTPException(
            status_code=403,
            detail=f"spend ceiling exceeded: {exc}",
            code=ErrorCode.SPEND_CEILING_EXCEEDED,
        ) from exc
    except LLMModelNotPriced as exc:
        raise CodedHTTPException(
            status_code=503,
            detail=f"model not priced: {exc}",
            code=ErrorCode.MODEL_NOT_PRICED,
        ) from exc
    except LLMTimeout as exc:
        raise CodedHTTPException(
            status_code=504,
            detail=f"provider timeout: {exc}",
            code=ErrorCode.PROVIDER_TIMEOUT,
        ) from exc
    except (LLMProviderUnavailable, LLMMalformedResponse) as exc:
        raise CodedHTTPException(
            status_code=502,
            detail=f"provider unavailable: {exc}",
            code=ErrorCode.PROVIDER_UNAVAILABLE,
        ) from exc
    except AgentOutputError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await _journal(container, ctx, row)

    return CoworkTurnResponse(
        reply=row.content_markdown,
        deliverable_id=row.id,
        agent_id=row.agent_id,
    )


async def _journal(container: Container, ctx: RequestContext, row: object) -> None:
    """Record the turn in the caller's own work journal.

    NOT IN THE DELIVERABLE'S TRANSACTION, and that is a known, pre-existing
    limitation rather than an oversight here. `WorkJournal.record`'s own contract
    asks to be called in the same transaction as the business write
    (app/principal/journal.py:85-87), which today would require restructuring
    `DeliverableService`'s connection boundary -- explicitly deferred at
    bootstrap.py, and out of scope for this route.

    Given that, the ordering is the safe one: the deliverable is already durable
    when this runs, so the failure mode is a MISSING journal line for an action
    that really happened, never a journal line for an action that did not. A
    failure is logged at ERROR naming the deliverable so the gap is findable, and
    is NOT re-raised: the deliverable is the system of record and the caller's
    turn genuinely succeeded. Telling them it failed would be the larger lie.
    """
    import logging

    log = logging.getLogger(__name__)
    deliverable_id = getattr(row, "id", None)
    try:
        await container.work_journal.record(
            org_id=ctx.org_id,
            principal_id=ctx.user_id,
            actor_kind=ActorKind.AGENT_COWORK,
            actor_id=COWORK_AGENT_ID,
            correlation_id=ctx.correlation_id,
            kind="cowork.turn",
            headline=f"Co-work turn produced {getattr(row, 'title', 'a reply')}",
            detail={"deliverable_id": str(deliverable_id)},
            governance_token_id=getattr(row, "governance_token_id", None),
        )
    except Exception:
        log.error(
            "cowork_journal_write_failed",
            extra={
                "deliverable_id": str(deliverable_id),
                "org_id": ctx.org_id,
                "principal_id": ctx.user_id,
            },
            exc_info=True,
        )
