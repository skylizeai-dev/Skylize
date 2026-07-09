"""Audit trail read routes — the governed-action feed the console renders.

Read-only. Rows come from the append-only `audit_log` (RLS + trigger-protected
in Postgres); payloads are SHA-256 hashes, never clear text, so this endpoint
exposes provenance — who did what, governed by which token, with what result —
without exposing content.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ...bootstrap import Container
from ...schemas.base import RequestContext
from ..deps import get_container, require_any_role_or_user

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


class AuditEntryResponse(BaseModel):
    event_id: UUID
    correlation_id: UUID
    action_type: str
    result: str
    occurred_at: datetime
    source_agent_id: str | None
    authority_level: str | None
    governance_token_id: UUID | None
    result_reason: str | None
    inputs_hash: str | None
    outputs_hash: str | None


class AuditListResponse(BaseModel):
    entries: list[AuditEntryResponse]
    # Pass this as `before` to fetch the next (older) page; None = no more.
    next_before: datetime | None


@router.get("", response_model=AuditListResponse)
async def list_audit(
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = None,
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin")),
    container: Container = Depends(get_container),
) -> AuditListResponse:
    if before is not None and before.tzinfo is None:
        raise HTTPException(status_code=422, detail="before must be timezone-aware (ISO 8601)")
    rows = await container.audit.recent(ctx.org_id, limit=limit, before=before)
    entries = [
        AuditEntryResponse(
            event_id=r.event_id,
            correlation_id=r.correlation_id,
            action_type=r.action_type,
            result=r.result,
            occurred_at=r.occurred_at,
            source_agent_id=r.source_agent_id,
            authority_level=r.authority_level,
            governance_token_id=r.governance_token_id,
            result_reason=r.result_reason,
            inputs_hash=r.inputs_hash,
            outputs_hash=r.outputs_hash,
        )
        for r in rows
    ]
    return AuditListResponse(
        entries=entries,
        next_before=entries[-1].occurred_at if len(entries) == limit else None,
    )
