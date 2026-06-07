"""Kill-switch control routes (owner-only)."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ...bootstrap import Container
from ...schemas.base import RequestContext
from ..deps import get_container, require_role

router = APIRouter(prefix="/api/v1/kill-switch", tags=["kill-switch"])

_SCOPES = {"agent", "department", "tenant", "platform"}


class KillSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_type: str
    scope_id: str
    reason: str = ""


@router.post("/engage")
async def engage(
    body: KillSwitchRequest,
    ctx: RequestContext = Depends(require_role("owner")),
    container: Container = Depends(get_container),
) -> dict[str, str]:
    assert body.scope_type in _SCOPES, "invalid scope_type"
    await container.authority.engage_kill_switch(
        scope_type=body.scope_type, scope_id=body.scope_id, org_id=ctx.org_id,
        engaged_by=ctx.user_id, reason=body.reason, correlation_id=uuid4(),
    )
    return {"status": "engaged", "scope_type": body.scope_type, "scope_id": body.scope_id}


@router.post("/disengage")
async def disengage(
    body: KillSwitchRequest,
    ctx: RequestContext = Depends(require_role("owner")),
    container: Container = Depends(get_container),
) -> dict[str, str]:
    await container.authority.disengage_kill_switch(
        scope_type=body.scope_type, scope_id=body.scope_id, org_id=ctx.org_id,
        disengaged_by=ctx.user_id, correlation_id=uuid4(),
    )
    return {"status": "disengaged", "scope_type": body.scope_type, "scope_id": body.scope_id}
