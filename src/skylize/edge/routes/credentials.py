"""
Credential vault routes.

Store / list / delete / resolve per-tenant provider credentials. Decrypted
values are returned ONLY via GET /resolve, which is restricted to owner/admin,
strictly org-scoped, rate-limited, and audit-logged. It exists so trusted
machine integrations (e.g. the Anytype sync job) can fetch a provider secret for
their own org via a service API key; in-process agents should prefer
CredentialVault.retrieve() directly.

  POST   /api/v1/credentials          — store (owner/admin)
  GET    /api/v1/credentials          — list provider names, never values (owner/admin/operator)
  GET    /api/v1/credentials/resolve  — decrypt one provider value (owner/admin)
  DELETE /api/v1/credentials/{id}     — remove by credential UUID (owner/admin)
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from ...app.credentials.vault import CredentialNotFoundError
from ...bootstrap import Container
from ...schemas.base import RequestContext
from ..deps import get_container, get_credential_resolve_limiter, require_any_role
from ..rate_limit import RateLimiter

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/credentials", tags=["credentials"])


class StoreCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=80)
    label: str = Field(default="", max_length=200)
    value: str = Field(min_length=1)
    metadata: dict[str, object] = Field(default_factory=dict)


class StoreCredentialResponse(BaseModel):
    cred_id: UUID
    provider: str
    label: str


class ProviderSummary(BaseModel):
    provider: str


class ResolvedCredential(BaseModel):
    provider: str
    value: str


@router.post("", response_model=StoreCredentialResponse, status_code=201)
async def store_credential(
    body: StoreCredentialRequest,
    ctx: RequestContext = Depends(require_any_role("owner", "admin")),
    container: Container = Depends(get_container),
) -> StoreCredentialResponse:
    cred_id = await container.credential_vault.store(
        org_id=ctx.org_id,
        provider=body.provider,
        raw_value=body.value,
        label=body.label,
        metadata=body.metadata,
        correlation_id=ctx.correlation_id,
    )
    return StoreCredentialResponse(cred_id=cred_id, provider=body.provider, label=body.label)


@router.get("", response_model=list[ProviderSummary])
async def list_providers(
    ctx: RequestContext = Depends(require_any_role("owner", "admin", "operator")),
    container: Container = Depends(get_container),
) -> list[ProviderSummary]:
    providers = await container.credential_vault.list_providers(ctx.org_id)
    return [ProviderSummary(provider=p) for p in providers]


@router.get("/resolve", response_model=ResolvedCredential)
async def resolve_credential(
    provider: str,
    label: str = "",
    ctx: RequestContext = Depends(require_any_role("owner", "admin")),
    container: Container = Depends(get_container),
    limiter: RateLimiter = Depends(get_credential_resolve_limiter),
) -> ResolvedCredential:
    """Return a decrypted credential value for the authenticated org.

    Only ever returns credentials owned by the caller's org_id. Every call is
    audit-logged. Rate-limited to 10 requests per minute per org.
    """
    if not limiter.allow(ctx.org_id):
        raise HTTPException(status_code=429, detail="credential resolve rate limit exceeded")
    log.info(
        "credential.resolve",
        org_id=ctx.org_id,
        provider=provider,
        user=ctx.user_id,
        correlation_id=str(ctx.correlation_id),
    )
    try:
        value = await container.credential_vault.retrieve(ctx.org_id, provider, label)
    except CredentialNotFoundError:
        raise HTTPException(status_code=404, detail="credential not found")
    return ResolvedCredential(provider=provider, value=value)


@router.delete("/{cred_id}", status_code=204)
async def delete_credential(
    cred_id: UUID,
    ctx: RequestContext = Depends(require_any_role("owner", "admin")),
    container: Container = Depends(get_container),
) -> Response:
    try:
        await container.credential_vault.delete_by_id(
            cred_id=cred_id,
            org_id=ctx.org_id,
            correlation_id=ctx.correlation_id,
        )
    except CredentialNotFoundError:
        raise HTTPException(status_code=404, detail="credential not found")
    return Response(status_code=204)
