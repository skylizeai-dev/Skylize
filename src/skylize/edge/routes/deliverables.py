"""Deliverables routes — agent output persistence and lifecycle."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from ...bootstrap import Container
from ...schemas.base import RequestContext
from ..deps import get_container, require_any_role_or_user

router = APIRouter(prefix="/api/v1/deliverables", tags=["deliverables"])

_VALID_TYPES = [
    "marketing_copy", "seo_report", "ad_creative", "strategy_doc",
    "social_post", "email_copy", "landing_page", "blog_post",
    "research_report", "competitor_analysis", "other",
]
_VALID_STATUSES = ["draft", "review", "approved", "revised", "archived"]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class CreateDeliverableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(min_length=1, max_length=200)
    deliverable_type: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=500)
    content_markdown: str = Field(min_length=1)
    summary: str | None = Field(default=None, max_length=500)
    governance_token_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_markdown: str = Field(min_length=1)
    summary: str | None = Field(default=None, max_length=500)


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved_by: str = Field(min_length=1, max_length=200)


class DeliverableResponse(BaseModel):
    id: UUID
    org_id: str
    agent_id: str
    deliverable_type: str
    title: str
    status: str
    version: int
    summary: str | None
    parent_id: UUID | None
    created_at: datetime
    updated_at: datetime
    approved_at: datetime | None
    approved_by: str | None


class DeliverableDetailResponse(DeliverableResponse):
    content_markdown: str
    metadata_json: dict[str, Any]
    governance_token_id: UUID | None


class PaginationMeta(BaseModel):
    total: int
    offset: int
    limit: int
    has_more: bool


class DeliverableListResponse(BaseModel):
    data: list[DeliverableResponse]
    pagination: PaginationMeta


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", response_model=DeliverableDetailResponse, status_code=201)
async def create_deliverable(
    body: CreateDeliverableRequest,
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin", "operator")),
    container: Container = Depends(get_container),
) -> DeliverableDetailResponse:
    from ...app.deliverables.service import DeliverableError

    try:
        row = await container.deliverables.create_deliverable(
            org_id=ctx.org_id,
            agent_id=body.agent_id,
            deliverable_type=body.deliverable_type,
            title=body.title,
            content_markdown=body.content_markdown,
            summary=body.summary or "",
            governance_token_id=body.governance_token_id,
            metadata=body.metadata,
        )
    except DeliverableError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _detail(row)


@router.get("", response_model=DeliverableListResponse)
async def list_deliverables(
    status: str | None = None,
    deliverable_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin", "operator", "analyst", "viewer")),
    container: Container = Depends(get_container),
) -> DeliverableListResponse:
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"invalid status: {status!r}")
    if deliverable_type is not None and deliverable_type not in _VALID_TYPES:
        raise HTTPException(status_code=422, detail=f"invalid deliverable_type: {deliverable_type!r}")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200")

    rows, total = await container.deliverables.list_deliverables(
        ctx.org_id,
        status=status,
        deliverable_type=deliverable_type,
        limit=limit,
        offset=offset,
    )
    return DeliverableListResponse(
        data=[_summary(r) for r in rows],
        pagination=PaginationMeta(
            total=total,
            offset=offset,
            limit=limit,
            has_more=offset + len(rows) < total,
        ),
    )


@router.get("/{id}", response_model=DeliverableDetailResponse)
async def get_deliverable(
    id: UUID,
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin", "operator", "analyst", "viewer")),
    container: Container = Depends(get_container),
) -> DeliverableDetailResponse:
    from ...app.deliverables.service import DeliverableNotFound

    try:
        row = await container.deliverables.get_deliverable(ctx.org_id, id)
    except DeliverableNotFound:
        raise HTTPException(status_code=404, detail="deliverable not found")
    return _detail(row)


@router.patch("/{id}/approve", response_model=DeliverableDetailResponse)
async def approve_deliverable(
    id: UUID,
    body: ApproveRequest,
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin")),
    container: Container = Depends(get_container),
) -> DeliverableDetailResponse:
    from ...app.deliverables.service import DeliverableError, DeliverableNotFound

    try:
        row = await container.deliverables.approve_deliverable(
            ctx.org_id, id, body.approved_by
        )
    except DeliverableNotFound:
        raise HTTPException(status_code=404, detail="deliverable not found")
    except DeliverableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _detail(row)


@router.patch("/{id}/revise", response_model=DeliverableDetailResponse)
async def revise_deliverable(
    id: UUID,
    body: ReviseRequest,
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin", "operator")),
    container: Container = Depends(get_container),
) -> DeliverableDetailResponse:
    from ...app.deliverables.service import DeliverableError, DeliverableNotFound

    try:
        row = await container.deliverables.revise_deliverable(
            ctx.org_id, id, body.content_markdown, body.summary or ""
        )
    except DeliverableNotFound:
        raise HTTPException(status_code=404, detail="deliverable not found")
    except DeliverableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _detail(row)


@router.patch("/{id}/archive", response_model=DeliverableDetailResponse)
async def archive_deliverable(
    id: UUID,
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin")),
    container: Container = Depends(get_container),
) -> DeliverableDetailResponse:
    from ...app.deliverables.service import DeliverableNotFound

    try:
        row = await container.deliverables.archive_deliverable(ctx.org_id, id)
    except DeliverableNotFound:
        raise HTTPException(status_code=404, detail="deliverable not found")
    return _detail(row)


@router.get("/{id}/versions", response_model=list[DeliverableResponse])
async def list_versions(
    id: UUID,
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin", "operator", "analyst", "viewer")),
    container: Container = Depends(get_container),
) -> list[DeliverableResponse]:
    from ...app.deliverables.service import DeliverableNotFound

    try:
        rows = await container.deliverables.list_versions(ctx.org_id, id)
    except DeliverableNotFound:
        raise HTTPException(status_code=404, detail="deliverable not found")
    return [_summary(r) for r in rows]


@router.get("/{id}/download")
async def download_deliverable(
    id: UUID,
    format: str = Query(default="md"),
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin", "operator", "analyst", "viewer")),
    container: Container = Depends(get_container),
) -> Response:
    from ...app.deliverables.exporters.base import ExportFormat
    from ...app.deliverables.exporters.factory import get_exporter
    from ...app.deliverables.service import DeliverableNotFound

    try:
        export_fmt = ExportFormat(format)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"unsupported format: {format!r}")

    try:
        row = await container.deliverables.get_deliverable(ctx.org_id, id)
    except DeliverableNotFound:
        raise HTTPException(status_code=404, detail="deliverable not found")

    exporter = get_exporter(export_fmt)
    data = exporter.export(row.content_markdown, row.title)
    fname = exporter.filename(row.title)
    return Response(
        content=data,
        media_type=exporter.content_type,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _summary(row: Any) -> DeliverableResponse:
    return DeliverableResponse(
        id=row.id,
        org_id=row.org_id,
        agent_id=row.agent_id,
        deliverable_type=row.deliverable_type,
        title=row.title,
        status=row.status,
        version=row.version,
        summary=row.summary,
        parent_id=row.parent_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        approved_at=row.approved_at,
        approved_by=row.approved_by,
    )


def _detail(row: Any) -> DeliverableDetailResponse:
    return DeliverableDetailResponse(
        id=row.id,
        org_id=row.org_id,
        agent_id=row.agent_id,
        deliverable_type=row.deliverable_type,
        title=row.title,
        status=row.status,
        version=row.version,
        summary=row.summary,
        parent_id=row.parent_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        approved_at=row.approved_at,
        approved_by=row.approved_by,
        content_markdown=row.content_markdown,
        metadata_json=row.metadata_json,
        governance_token_id=row.governance_token_id,
    )
