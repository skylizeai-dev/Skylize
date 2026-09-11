"""HITL queue routes — the human side of the synchronous decision gate.

GET  /api/v1/hitl                     — pending items for the caller's org
POST /api/v1/hitl/{hitl_id}/approve   — record verdict + execute the deferred work
POST /api/v1/hitl/{hitl_id}/reject    — record verdict; nothing executes

Org scoping comes from the authenticated principal (RequestContext.org_id),
never from a query/body field. RBAC mirrors the deliverable-approval seam:
`require_any_role_or_user("owner", "admin")` — operators may request work but
do not clear governance escalations; analyst/viewer are read-only personas and
this queue exists to act.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ...bootstrap import Container
from ...dal.ports import HitlQueueItem
from ...schemas.base import RequestContext
from ..deps import get_container, require_any_role_or_user

router = APIRouter(prefix="/api/v1/hitl", tags=["hitl"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class HitlVerdictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=2000)


class HitlItemResponse(BaseModel):
    hitl_id: UUID
    agent_id: str | None
    trigger_reason: str
    status: str
    created_at: datetime
    expires_at: datetime | None
    # WHAT WAS DECIDED (action_kind / department / proposing agent) …
    proposal_summary: dict[str, Any]
    # … and WHAT WOULD EXECUTE if approved (the replay envelope's input).
    request_input: dict[str, Any] | None
    # WHAT APPROVAL MEANS for this row. The two shapes mean materially different
    # things and coexist on this queue permanently, because the request-level
    # gate is not going away:
    #   "rerun_request" — the agent is re-run on `request_input` and THE MODEL
    #                     MAY DECIDE DIFFERENTLY. Every stage-2.5 deferral, and
    #                     every row written before migration 0027.
    #   "resume_action" — exactly the calls in `pending_tool_calls` execute,
    #                     verbatim, with the ids and inputs shown here. Turns
    #                     AFTER this one are still sampled fresh, so the honest
    #                     claim is "approving executes THIS call", never
    #                     "approving executes only this call".
    # Serving them as one undifferentiated queue is the disclosure gap the
    # resumption work exists to close, so the distinction is stated, not implied.
    approval_semantics: Literal["rerun_request", "resume_action"]
    # The reviewed calls themselves, on a "resume_action" row only. Read out of
    # the stored assistant turn, so this is literally what will be dispatched.
    pending_tool_calls: list[dict[str, Any]] | None


class PaginationMeta(BaseModel):
    total: int
    offset: int
    limit: int
    has_more: bool


class HitlListResponse(BaseModel):
    data: list[HitlItemResponse]
    pagination: PaginationMeta


class HitlApproveResponse(BaseModel):
    hitl_id: UUID
    status: str
    deliverable_id: UUID
    agent_id: str
    title: str


class HitlRejectResponse(BaseModel):
    hitl_id: UUID
    status: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=HitlListResponse)
async def list_pending(
    limit: int = 50,
    offset: int = 0,
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin")),
    container: Container = Depends(get_container),
) -> HitlListResponse:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0")
    rows, total = await container.hitl.list_pending(ctx.org_id, limit=limit, offset=offset)
    return HitlListResponse(
        data=[_summary(r) for r in rows],
        pagination=PaginationMeta(
            total=total, offset=offset, limit=limit,
            has_more=offset + len(rows) < total,
        ),
    )


@router.post("/{hitl_id}/approve", response_model=HitlApproveResponse)
async def approve(
    hitl_id: UUID,
    body: HitlVerdictRequest | None = None,
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin")),
    container: Container = Depends(get_container),
) -> HitlApproveResponse:
    from ...app.hitl.service import (
        HitlAlreadyActioned,
        HitlDeferredAgain,
        HitlExecutionFailed,
        HitlExpired,
        HitlNotFound,
        HitlNotReplayable,
        HitlReplayInvalid,
    )

    try:
        row, deliverable = await container.hitl.approve(
            org_id=ctx.org_id,
            hitl_id=hitl_id,
            reviewed_by=ctx.user_id,
            note=body.note if body else None,
        )
    except HitlNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HitlAlreadyActioned as exc:
        # Idempotency: the second verdict on a row gets a 409 naming the
        # existing status — truthful and safe; it never re-executes.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HitlExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except HitlNotReplayable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HitlReplayInvalid as exc:
        # K7: stored payload no longer validates against the CURRENT schema (or
        # the contract is gone). PERMANENT under owner decision D4 — request_json
        # is frozen at enqueue, so the row was moved to the TERMINAL 'expired'
        # status, not released to 'pending'. Nothing executed; a retry is
        # impossible by design (it would fail identically forever).
        raise HTTPException(status_code=422, detail=f"replay invalid: {exc}") from exc
    except HitlDeferredAgain as exc:
        # 202, the same code /agents/execute uses to say "a human must decide
        # this": the approval WAS applied and the run WAS executed, and it then
        # hit a narrower gate that raised a new row. Not an error, and not a
        # retry — this row is now 'approved' and a second POST here gets the 409
        # above. The caller must act on the hitl_id named in the detail.
        raise HTTPException(
            status_code=202,
            detail=f"deferred again: {exc}; act on hitl_id={exc.hitl_id}",
        ) from exc
    except HitlExecutionFailed as exc:
        # K12: execution failed after the claim for a TRANSIENT reason. The row
        # WAS released back to 'pending' so the approved work is not lost; retry
        # after fixing. This is the only 502 the approve path itself produces.
        raise HTTPException(status_code=502, detail=f"execution failed: {exc}") from exc
    return HitlApproveResponse(
        hitl_id=row.hitl_id,
        status="approved",
        deliverable_id=deliverable.id,
        agent_id=deliverable.agent_id,
        title=deliverable.title,
    )


@router.post("/{hitl_id}/reject", response_model=HitlRejectResponse)
async def reject(
    hitl_id: UUID,
    body: HitlVerdictRequest | None = None,
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin")),
    container: Container = Depends(get_container),
) -> HitlRejectResponse:
    from ...app.hitl.service import (
        HitlAlreadyActioned,
        HitlExpired,
        HitlNotFound,
    )

    try:
        row = await container.hitl.reject(
            org_id=ctx.org_id,
            hitl_id=hitl_id,
            reviewed_by=ctx.user_id,
            note=body.note if body else None,
        )
    except HitlNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HitlAlreadyActioned as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HitlExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    return HitlRejectResponse(hitl_id=row.hitl_id, status="rejected")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _pending_tool_calls(resumption: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    """The reviewed turn's tool calls, rendered for the reviewer.

    Read out of the STORED assistant message rather than reconstructed, so what
    the human is shown is the same object the resumed run dispatches. Reads the
    serialized shape by key (the row is untyped JSON at this seam, as
    `request_json` already is here) and returns None for a request-level row.
    """
    if not resumption:
        return None
    messages = resumption.get("messages") or []
    if not messages:
        return None
    tail = messages[-1]
    return [
        {
            "tool_use_id": block.get("tool_use_id"),
            "tool_name": block.get("tool_name"),
            "tool_input": block.get("tool_input"),
        }
        for block in (tail.get("content") or [])
        if isinstance(block, dict) and block.get("kind") == "tool_use"
    ]


def _summary(row: HitlQueueItem) -> HitlItemResponse:
    request = row.request_json or {}
    agent_id = request.get("agent_id") or row.proposal_json.get("proposing_agent_id")
    resuming = row.resumption_json is not None
    return HitlItemResponse(
        hitl_id=row.hitl_id,
        agent_id=str(agent_id) if agent_id else None,
        trigger_reason=row.trigger_reason,
        status=row.status,
        created_at=row.created_at,
        expires_at=row.expires_at,
        proposal_summary={
            key: row.proposal_json.get(key)
            for key in ("action_kind", "department", "proposing_agent_id")
        },
        request_input=request.get("input"),
        approval_semantics="resume_action" if resuming else "rerun_request",
        pending_tool_calls=_pending_tool_calls(row.resumption_json),
    )
