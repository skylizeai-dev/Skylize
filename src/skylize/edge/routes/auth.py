"""
Human-user authentication routes.

  POST /api/v1/auth/register  — create account
  POST /api/v1/auth/login     — email + password → access + refresh tokens
  POST /api/v1/auth/refresh   — rotate refresh token → new pair
  GET  /api/v1/auth/me        — current user info (requires valid access token)

No rate limiting here beyond the global gateway limiter; add per-endpoint
throttling (login attempts, register spam) in a later sprint.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from ...app.auth.user_service import DuplicateEmailError, InvalidCredentialsError
from ...bootstrap import Container
from ...memory.identity import InvalidIdentifier, validate_identifier
from ...schemas.base import RequestContext
from ..deps import get_container, get_current_user

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# ── request/response models ────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    org_id: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=120)

    @field_validator("org_id")
    @classmethod
    def _validate_org_id(cls, value: str) -> str:
        # org_id becomes a tenant-isolation key, so it must be a strict slug —
        # no ':' or other separators that could forge a namespace boundary.
        # `memory.identity` is the single source of truth for the slug rules;
        # it only accepts lowercase, so normalize case here (the one place
        # org_ids are minted) rather than rejecting mixed-case registrations.
        try:
            return validate_identifier(value.lower(), field="org_id")
        except InvalidIdentifier as exc:
            raise ValueError(str(exc)) from exc


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=1)


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    user_id: UUID
    org_id: str
    email: str
    display_name: str | None
    roles: list[str]
    is_active: bool


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.post("/register", response_model=UserResponse, status_code=201)
async def register(
    body: RegisterRequest,
    container: Container = Depends(get_container),
) -> UserResponse:
    try:
        user = await container.user_auth.register(
            org_id=body.org_id,
            email=body.email,
            password=body.password,
            display_name=body.display_name,
        )
    except DuplicateEmailError:
        raise HTTPException(status_code=409, detail="email already registered")
    return UserResponse(
        user_id=user.user_id,
        org_id=user.org_id,
        email=user.email,
        display_name=user.display_name,
        roles=user.roles,
        is_active=user.is_active,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    container: Container = Depends(get_container),
) -> TokenResponse:
    try:
        result = await container.user_auth.login(
            email=body.email, password=body.password
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    return TokenResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    container: Container = Depends(get_container),
) -> TokenResponse:
    try:
        result = await container.user_auth.refresh(refresh_token=body.refresh_token)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    return TokenResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
    )


@router.get("/me", response_model=UserResponse)
async def me(
    ctx: RequestContext = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> UserResponse:
    try:
        user_id = UUID(ctx.user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid user_id in token")
    user = await container.user_auth.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return UserResponse(
        user_id=user.user_id,
        org_id=user.org_id,
        email=user.email,
        display_name=user.display_name,
        roles=user.roles,
        is_active=user.is_active,
    )
