"""
Tenant onboarding + RBAC routes (Subsystem 1).

Registration provisions the org named by the caller's verified context
(``org_id`` from the OIDC token / dev headers) and seeds that caller as owner —
no privilege paradox, since the IdP already authenticated them. User/role
management and status changes are gated by RBAC on the same context.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from ...app.tenants.service import TenantError
from ...bootstrap import Container
from ...schemas.base import RequestContext
from ..deps import get_container, get_context, require_any_role, require_role

router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"])


class RegisterTenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=200)
    oidc_issuer: str = "dev"


class TenantResponse(BaseModel):
    org_id: str
    display_name: str
    status: str


class SetRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str


class UserResponse(BaseModel):
    user_id: str
    role: str


@router.post("", response_model=TenantResponse, status_code=201)
async def register_tenant(
    body: RegisterTenantRequest,
    ctx: RequestContext = Depends(get_context),
    container: Container = Depends(get_container),
) -> TenantResponse:
    try:
        row = await container.tenants.register(
            org_id=ctx.org_id,
            display_name=body.display_name,
            owner_user_id=ctx.user_id,
            oidc_issuer=body.oidc_issuer,
            correlation_id=ctx.correlation_id,
        )
    except TenantError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return TenantResponse(org_id=row.org_id, display_name=row.display_name, status=row.status)


@router.get("/me", response_model=TenantResponse)
async def my_tenant(
    ctx: RequestContext = Depends(get_context),
    container: Container = Depends(get_container),
) -> TenantResponse:
    row = await container.tenants.get(ctx.org_id)
    if row is None:
        raise HTTPException(status_code=404, detail="tenant not registered")
    return TenantResponse(org_id=row.org_id, display_name=row.display_name, status=row.status)


@router.post("/me/suspend", response_model=TenantResponse)
async def suspend_tenant(
    ctx: RequestContext = Depends(require_role("owner")),
    container: Container = Depends(get_container),
) -> TenantResponse:
    try:
        row = await container.tenants.set_status(
            org_id=ctx.org_id, status="suspended", actor=ctx.user_id,
            correlation_id=ctx.correlation_id,
        )
    except TenantError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return TenantResponse(org_id=row.org_id, display_name=row.display_name, status=row.status)


@router.post("/me/reactivate", response_model=TenantResponse)
async def reactivate_tenant(
    ctx: RequestContext = Depends(require_role("owner")),
    container: Container = Depends(get_container),
) -> TenantResponse:
    try:
        row = await container.tenants.set_status(
            org_id=ctx.org_id, status="active", actor=ctx.user_id,
            correlation_id=ctx.correlation_id,
        )
    except TenantError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return TenantResponse(org_id=row.org_id, display_name=row.display_name, status=row.status)


@router.get("/me/users", response_model=list[UserResponse])
async def list_users(
    ctx: RequestContext = Depends(require_any_role("owner", "admin")),
    container: Container = Depends(get_container),
) -> list[UserResponse]:
    return [
        UserResponse(user_id=u.user_id, role=u.role)
        for u in await container.tenants.list_users(ctx.org_id)
    ]


@router.put("/me/users/{user_id}", response_model=UserResponse)
async def set_user_role(
    user_id: str,
    body: SetRoleRequest,
    ctx: RequestContext = Depends(require_any_role("owner", "admin")),
    container: Container = Depends(get_container),
) -> UserResponse:
    try:
        row = await container.tenants.set_user_role(
            org_id=ctx.org_id, user_id=user_id, role=body.role, actor=ctx.user_id,
            correlation_id=ctx.correlation_id,
        )
    except TenantError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UserResponse(user_id=row.user_id, role=row.role)


@router.delete("/me/users/{user_id}", status_code=204)
async def remove_user(
    user_id: str,
    ctx: RequestContext = Depends(require_role("owner")),
    container: Container = Depends(get_container),
) -> Response:
    try:
        await container.tenants.remove_user(
            org_id=ctx.org_id, user_id=user_id, actor=ctx.user_id,
            correlation_id=ctx.correlation_id,
        )
    except TenantError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)
