"""Notification feed routes — the console's Notifications screen.

Two verbs: list (newest first, optional unread filter) and mark-read. Both are
org-scoped through an RLS-scoped DAL, with `org_id` taken strictly from the
authenticated `RequestContext` and never from a query parameter or body field,
the same rule the spend and autonomy routes follow.

WHAT FILLS THIS FEED, AND WHAT DOES NOT. Rows come from exactly two real
producers, both inside `AgentExecutionService`:

  * `hitl.approval_requested` — written by `_enqueue_hitl`, beside the Slack
    post, every time a governed action is deferred to a human;
  * `governance.action_denied` — written by `_govern` when the evaluator refuses
    a proposed action outright.

Nothing seeds this table. In a fresh environment the list is EMPTY until one of
those two things actually happens, and that empty list is the correct answer —
it says the org's agents have not yet needed a human or been refused. A screen
populated with invented rows would be a false claim in the one surface whose job
is to report what the governance path really did.

RBAC is `require_any_role_or_user("owner", "admin")` on BOTH verbs, matching the
audit feed. The read gate and the org scope agree deliberately: the table is
org-scoped (see migration 0034 for why per-user delivery was refused rather than
guessed), so the people who can see the feed are the people the feed is for, and
marking a row read is an org-level acknowledgement rather than a personal one.
Mark-read is a POST on a sub-path rather than a PATCH on the row, matching the
HITL verdict routes (`POST /api/v1/hitl/{id}/approve`) — the repo's convention
for "apply this named action to this row".
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ...bootstrap import Container
from ...dal.notifications import NotificationRow, NotificationsDAL
from ...schemas.base import RequestContext
from ..deps import get_container, require_any_role_or_user

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


class NotificationResponse(BaseModel):
    notification_id: UUID
    kind: str
    severity: str
    title: str
    body: str
    # The correlation id of the governed action behind this notification — the
    # SAME id the audit feed exposes, so the console can link one to the other.
    # None when the producer genuinely had none.
    correlation_id: UUID | None
    created_at: datetime
    read_at: datetime | None


class NotificationListResponse(BaseModel):
    notifications: list[NotificationResponse]
    # Unread across the WHOLE org, not just this page — the badge count.
    unread_count: int
    # Pass this as `before` to fetch the next (older) page; None = no more.
    next_before: datetime | None


def _require_dal(container: Container) -> NotificationsDAL:
    if container.notifications_dal is None:
        raise HTTPException(
            status_code=503,
            detail="notifications require the postgres backend",
        )
    return container.notifications_dal


def _to_response(row: NotificationRow) -> NotificationResponse:
    return NotificationResponse(
        notification_id=row.notification_id,
        kind=row.kind,
        severity=row.severity,
        title=row.title,
        body=row.body,
        correlation_id=row.correlation_id,
        created_at=row.created_at,
        read_at=row.read_at,
    )


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    limit: int = Query(default=50, ge=1, le=200),
    unread_only: bool = Query(default=False),
    before: datetime | None = None,
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin")),
    container: Container = Depends(get_container),
) -> NotificationListResponse:
    if before is not None and before.tzinfo is None:
        raise HTTPException(
            status_code=422, detail="before must be timezone-aware (ISO 8601)"
        )
    dal = _require_dal(container)
    rows = await dal.list_for_org(
        ctx.org_id, limit=limit, unread_only=unread_only, before=before
    )
    items = [_to_response(r) for r in rows]
    return NotificationListResponse(
        notifications=items,
        unread_count=await dal.unread_count(ctx.org_id),
        next_before=items[-1].created_at if len(items) == limit else None,
    )


@router.post("/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(
    notification_id: UUID,
    ctx: RequestContext = Depends(require_any_role_or_user("owner", "admin")),
    container: Container = Depends(get_container),
) -> NotificationResponse:
    """Acknowledge one notification. Idempotent — a second call is a no-op that
    returns the row with its ORIGINAL `read_at`, because the first
    acknowledgement is the one that happened.

    404 covers both "no such id" and "that id belongs to another org": RLS makes
    the second case indistinguishable from the first at the DAL, which is the
    correct answer — confirming the existence of another tenant's row would be a
    cross-tenant leak in itself.
    """
    dal = _require_dal(container)
    row = await dal.mark_read(org_id=ctx.org_id, notification_id=notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="notification not found")
    return _to_response(row)
