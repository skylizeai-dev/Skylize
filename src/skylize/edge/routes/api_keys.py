"""
API-key management routes (Subsystem 1).

Issue / list / revoke programmatic keys for agent-to-agent access. The plaintext
secret is present in exactly one response — the 201 from issuance — and never
again: list responses carry metadata only (no secret, no hash). All three
operations require owner or admin on the calling context.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from ...bootstrap import Container
from ...schemas.base import RequestContext
from ..deps import get_container, require_any_role

router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])


class IssueKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=list)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class IssuedKeyResponse(BaseModel):
    key_id: UUID
    prefix: str
    name: str
    scopes: list[str]
    api_key: str  # plaintext — shown exactly once
    expires_at: datetime | None


class KeyResponse(BaseModel):
    key_id: UUID
    prefix: str
    name: str
    scopes: list[str]
    created_by: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


@router.post("", response_model=IssuedKeyResponse, status_code=201)
async def issue_key(
    body: IssueKeyRequest,
    ctx: RequestContext = Depends(require_any_role("owner", "admin")),
    container: Container = Depends(get_container),
) -> IssuedKeyResponse:
    expires_at: datetime | None = None
    if body.expires_in_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
    row, secret = await container.api_keys.issue(
        org_id=ctx.org_id, name=body.name, scopes=body.scopes, created_by=ctx.user_id,
        correlation_id=ctx.correlation_id, expires_at=expires_at,
    )
    return IssuedKeyResponse(
        key_id=row.key_id, prefix=row.prefix, name=row.name, scopes=row.scopes,
        api_key=secret, expires_at=row.expires_at,
    )


@router.get("", response_model=list[KeyResponse])
async def list_keys(
    ctx: RequestContext = Depends(require_any_role("owner", "admin")),
    container: Container = Depends(get_container),
) -> list[KeyResponse]:
    return [
        KeyResponse(
            key_id=k.key_id, prefix=k.prefix, name=k.name, scopes=k.scopes,
            created_by=k.created_by, created_at=k.created_at, expires_at=k.expires_at,
            last_used_at=k.last_used_at, revoked_at=k.revoked_at,
        )
        for k in await container.api_keys.list(ctx.org_id)
    ]


@router.delete("/{key_id}", status_code=204)
async def revoke_key(
    key_id: UUID,
    ctx: RequestContext = Depends(require_any_role("owner", "admin")),
    container: Container = Depends(get_container),
) -> Response:
    revoked = await container.api_keys.revoke(
        org_id=ctx.org_id, key_id=key_id, actor=ctx.user_id, reason="revoked via API",
        correlation_id=ctx.correlation_id,
    )
    if not revoked:
        raise HTTPException(status_code=404, detail="api key not found")
    return Response(status_code=204)
