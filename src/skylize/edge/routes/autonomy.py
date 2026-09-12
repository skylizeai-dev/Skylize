"""Org autonomy mode route — read and set the ORG-WIDE autonomy posture.

ORG-WIDE (ruling 6): one GET and one PUT, no department or agent path segment,
because there is no narrower row to address.

Conventions follow the spend position route (edge/routes/spend.py): typed
Pydantic models, org_id strictly from the authenticated RequestContext and never
from a query parameter or body field, and an RLS-scoped DAL underneath.

RBAC is split deliberately. The GET is owner-or-admin, like every other console
read. The PUT is OWNER ONLY, matching the kill switch (edge/routes/kill_switch.py)
rather than the broader read role: changing this value changes how much the whole
organization's agents may do without a person, which is the owner's decision.

FAIL CLOSED (ruling 7): the GET reports `observe` whenever no row resolves. The
`configured` flag tells the console whether that came from a choice or from the
absence of one; `mode` is safe to act on either way.

NOT WIRED TO THE CONSOLE UI. The console's autonomy setting is still backed by
localStorage. Connecting the two is deliberate follow-up work, once this backend
is tested and merged.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...bootstrap import Container
from ...contracts.base import AutonomyMode
from ...dal.org_autonomy_mode import OrgAutonomyModeDAL
from ...schemas.base import RequestContext
from ..deps import get_container, require_any_role_or_user, require_role

router = APIRouter(prefix="/api/v1/autonomy", tags=["autonomy"])


class AutonomyModeResponse(BaseModel):
    mode: AutonomyMode = Field(
        description="The org-wide autonomy mode in force. 'observe' when the "
        "org has never set one — fail closed, ruling 7."
    )
    configured: bool = Field(
        description="False when no row resolves for this org, i.e. the 'observe' "
        "above is the fail-closed default rather than a choice. Display only; "
        "enforcement acts on `mode` either way."
    )


class SetAutonomyModeRequest(BaseModel):
    mode: AutonomyMode = Field(
        description="The mode to put in force from now on. One of the five "
        "named modes; anything else is rejected by the schema, by the DAL, and "
        "by the table's CHECK constraint."
    )


def _require_dal(container: Container) -> "OrgAutonomyModeDAL":
    if container.autonomy_mode_dal is None:
        raise HTTPException(
            status_code=503,
            detail="autonomy mode requires the postgres backend",
        )
    return container.autonomy_mode_dal


@router.get("", response_model=AutonomyModeResponse)
async def get_autonomy_mode(
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin")),
    container: Container = Depends(get_container),
) -> AutonomyModeResponse:
    dal = _require_dal(container)
    configured = await dal.read_configured_mode(ctx.org_id)
    if configured is None:
        # Not a second source of truth for the default: read_mode owns it, and
        # this branch exists only to report WHY the answer is `observe`.
        return AutonomyModeResponse(mode=await dal.read_mode(ctx.org_id), configured=False)
    return AutonomyModeResponse(mode=configured, configured=True)


@router.put("", response_model=AutonomyModeResponse)
async def set_autonomy_mode(
    body: SetAutonomyModeRequest,
    ctx: RequestContext = Depends(require_role("owner")),
    container: Container = Depends(get_container),
) -> AutonomyModeResponse:
    dal = _require_dal(container)
    mode = await dal.set_mode(
        org_id=ctx.org_id,
        autonomy_mode=body.mode,
        audit=container.audit,
        correlation_id=ctx.correlation_id,
    )
    return AutonomyModeResponse(mode=mode, configured=True)
