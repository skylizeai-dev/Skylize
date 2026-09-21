"""Permission matrix read route — GET /api/v1/permissions/matrix.

Serves the console's Permission-matrix screen: for every RAW ROUTE GROUP (a
route file under `edge/routes/`, e.g. "kill_switch", "tenants", "agents"), and
for each of the 5 platform roles, whether that role can reach at least one GET
(read) and at least one POST/PUT/PATCH/DELETE (write) route in that group,
per the ACTUAL `Depends(require_role(...))` / `Depends(require_any_role(...))`
/ `Depends(require_any_role_or_user(...))` literals on the real handlers
(`edge/permission_matrix.py`). Zero invented vocabulary, zero hand-transcribed
rows: the response is a direct rendering of `build_permission_matrix()`.

READ-ONLY. This route computes; it writes nothing and gates no other route.

RBAC — AND WHY GETTING IT WRONG HERE IS NOT COSMETIC. This endpoint's whole
payload is a description of the platform's own authority system: which roles
can reach which control surfaces. An over-wide gate on THIS route would leak
that shape (e.g. "the kill switch is owner-only", "tenants can be removed by
owner-only") to a caller who should not see it, which is itself a disclosure
about the governance system distinct from anything a normal data route leaks.
`require_any_role_or_user("owner", "admin")` is chosen because it is the exact
gate every other console read in this codebase uses (audit.py, security.py,
spend.py, billing.py, autonomy.py's GET, models.py, notifications.py,
workflows.py) — this route reveals no more than a console operator sees
already reflected in which screens they can open, so widening or narrowing it
relative to that convention would be the actual mistake, not matching it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..deps import require_any_role_or_user
from ..permission_matrix import ALL_ROLES, PermissionMatrix, build_permission_matrix
from ...schemas.base import RequestContext

router = APIRouter(prefix="/api/v1/permissions", tags=["permissions"])


class RoleAccessResponse(BaseModel):
    read: bool = Field(
        description="True if this role can reach at least one GET/HEAD route "
        "in this route group, per a real Depends(require_*(...)) call site."
    )
    write: bool = Field(
        description="True if this role can reach at least one POST/PUT/PATCH/"
        "DELETE route in this route group, per a real Depends(require_*(...)) "
        "call site."
    )


class RouteGroupResponse(BaseModel):
    route_group: str = Field(
        description="The route module's file stem — the RAW group name, e.g. "
        "'kill_switch', 'tenants', 'agents'. Never a business-action label."
    )
    route_count: int = Field(
        description="Distinct (method, path) pairs in this group that carry a "
        "role gate. A route group can be non-zero here while still resolving "
        "some roles to false, e.g. a group that is entirely owner-only."
    )
    access: dict[str, RoleAccessResponse] = Field(
        description="Keyed by role name; always exactly the 5 platform roles."
    )


class PermissionMatrixResponse(BaseModel):
    generated_at: str = Field(
        description="UTC ISO-8601 timestamp of THIS scan, not a cached value — "
        "the route files are re-parsed on every request (see "
        "edge/permission_matrix.py's module docstring for why no cache is "
        "used). A client must never treat this as a staleness guarantee "
        "beyond 'the source tree as of this exact response'."
    )
    roles: list[str] = Field(
        description="The 5 platform roles, in the response's column order."
    )
    route_groups: list[RouteGroupResponse]


def _to_response(matrix: PermissionMatrix) -> PermissionMatrixResponse:
    return PermissionMatrixResponse(
        generated_at=matrix.generated_at.isoformat(),
        roles=list(ALL_ROLES),
        route_groups=[
            RouteGroupResponse(
                route_group=g.route_group,
                route_count=g.route_count,
                access={
                    role: RoleAccessResponse(read=acc.read, write=acc.write)
                    for role, acc in g.access.items()
                },
            )
            for g in matrix.route_groups
        ],
    )


@router.get("/matrix", response_model=PermissionMatrixResponse)
async def get_permission_matrix(
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin")),
) -> PermissionMatrixResponse:
    matrix = build_permission_matrix()
    return _to_response(matrix)
