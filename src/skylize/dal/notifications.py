"""Notifications DAL — the console's notification feed (migration 0034).

Read/write layer for `notifications`: the durable, org-scoped record of the
noteworthy things the system already knows happened, which the console's
Notifications screen lists and marks read.

ORG-SCOPED, NOT PER-USER. Migration 0034's docstring carries the full argument;
the short form is that the events behind these rows (a HITL approval request
above all) are addressed to a ROLE, not to a named person — `hitl_queue` itself
has no addressee column, only a `reviewed_by` captured at verdict time — so a
per-user column could only be filled with a guess, and a wrong guess hides an
approval request from the person who would have actioned it.

Every query runs inside `Database.tenant_session(org_id)` so the RLS
`tenant_isolation` policy applies — one org can never read, write or acknowledge
another org's notifications — exactly as `OrgAutonomyModeDAL` is scoped.

BEST-EFFORT ON THE WRITE PATH. `record` swallows and logs every exception. It is
called alongside actions that are already durable by the time it runs (the
`hitl_queue` row, the audit record), so a notification failure must never fail
the action that produced it — the same rule `SlackApprovalNotifier` follows, for
the same reason. It returns the new id on success and None when it gave up, so a
caller that cares can tell, and no caller has to.

NOT AN AUDIT TRAIL. Everything worth auditing already has an `audit_log` row
under the same `correlation_id`. This table is a convenience surface over those
facts and must never be read as a substitute for them; that is why it has no
append-only trigger and why `read_at` is a plain mutable column.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from skylize.dal.connection import Database

log = logging.getLogger(__name__)

#: Notification kinds, mirroring the CHECK constraint in migration 0034.
#:
#: EVERY VALUE HERE HAS A REAL PRODUCER. The set is deliberately small: it names
#: the events the system actually raises today, not the events a mock screen
#: displayed. Adding a member without wiring a producer would put a kind in the
#: type that no row can ever carry.
#: Exactly TWO members, and there is no third because there is no third
#: producer. `hitl.approval_expired` was considered and REJECTED: expiry is
#: detected lazily inside `HitlQueueService._raise_refusal` when somebody tries
#: to action an already-expired row, and there is no scheduled sweep that would
#: notice an escalation timing out unobserved. A kind whose only trigger is
#: somebody already looking at the row is not a notification.
NotificationKind = Literal[
    # Raised by AgentExecutionService._enqueue_hitl, beside the Slack post, on
    # every deferral to a human (both the request-level and the mid-loop gate,
    # because both route through _defer_to_human -> _enqueue_hitl).
    "hitl.approval_requested",
    # Raised by AgentExecutionService._govern when the evaluator refuses the
    # proposed action outright (outcome == "rejected" -> AgentGovernanceRejected).
    "governance.action_denied",
]

NotificationSeverity = Literal["info", "warning", "critical"]

VALID_KINDS: frozenset[str] = frozenset(
    {"hitl.approval_requested", "governance.action_denied"}
)
VALID_SEVERITIES: frozenset[str] = frozenset({"info", "warning", "critical"})

#: Hard ceiling on a single list page, mirroring the audit feed's own cap.
MAX_LIST_LIMIT = 200


@dataclass(frozen=True)
class NotificationRow:
    notification_id: UUID
    org_id: str
    kind: str
    severity: str
    title: str
    body: str
    correlation_id: UUID | None
    created_at: datetime
    read_at: datetime | None


class NotificationsDAL:
    def __init__(self, db: "Database") -> None:
        self._db = db

    async def record(
        self,
        *,
        org_id: str,
        kind: NotificationKind,
        severity: NotificationSeverity,
        title: str,
        body: str,
        correlation_id: UUID | None = None,
        notification_id: UUID | None = None,
    ) -> UUID | None:
        """Insert one notification. BEST EFFORT: never raises.

        The caller is always an action that has already committed something
        durable — a `hitl_queue` row, an audit record — so losing the
        convenience row must not lose the action. Failures are logged and None
        is returned, mirroring `SlackApprovalNotifier.notify_pending_approval`
        and `HitlQueueService._journal_replay`.

        `kind` and `severity` are validated here as well as by the table's CHECK
        constraints, so a bad value from a future caller is a legible log line
        rather than a raw constraint violation buried in a swallowed exception.

        `notification_id` lets a caller own the id (for idempotent replay). It
        is minted here otherwise.
        """
        new_id = notification_id if notification_id is not None else uuid4()
        if kind not in VALID_KINDS:
            log.error(
                "notification_record_failed",
                extra={"org_id": org_id, "reason": f"unknown kind {kind!r}"},
            )
            return None
        if severity not in VALID_SEVERITIES:
            log.error(
                "notification_record_failed",
                extra={"org_id": org_id, "reason": f"unknown severity {severity!r}"},
            )
            return None
        try:
            async with self._db.tenant_session(org_id) as conn:
                await conn.execute(
                    """
                    INSERT INTO notifications (
                        notification_id, org_id, kind, severity,
                        title, body, correlation_id
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (notification_id) DO NOTHING
                    """,
                    new_id,
                    org_id,
                    kind,
                    severity,
                    title,
                    body,
                    correlation_id,
                )
        except Exception:
            log.error(
                "notification_record_failed",
                extra={"org_id": org_id, "kind": kind},
                exc_info=True,
            )
            return None
        return new_id

    async def list_for_org(
        self,
        org_id: str,
        *,
        limit: int = 50,
        unread_only: bool = False,
        before: datetime | None = None,
    ) -> list[NotificationRow]:
        """Newest first, optionally unread only, optionally keyset-paged.

        `before` is the same keyset cursor shape the audit feed uses: pass the
        previous page's oldest `created_at` to get the next (older) page. The
        `(org_id, created_at DESC)` index from migration 0034 serves both the
        filtered and the unfiltered form.

        Tenant-scoped via RLS, so the `org_id` predicate below is belt over
        braces — the policy already makes another org's rows invisible.
        """
        capped = max(1, min(limit, MAX_LIST_LIMIT))
        conditions = ["org_id = $1"]
        args: list[object] = [org_id]
        if unread_only:
            conditions.append("read_at IS NULL")
        if before is not None:
            args.append(before)
            conditions.append(f"created_at < ${len(args)}")
        args.append(capped)
        query = (
            "SELECT notification_id, org_id, kind, severity, title, body, "
            "correlation_id, created_at, read_at FROM notifications WHERE "
            + " AND ".join(conditions)
            + f" ORDER BY created_at DESC LIMIT ${len(args)}"
        )
        async with self._db.tenant_session(org_id) as conn:
            rows = await conn.fetch(query, *args)
        return [
            NotificationRow(
                notification_id=r["notification_id"],
                org_id=r["org_id"],
                kind=r["kind"],
                severity=r["severity"],
                title=r["title"],
                body=r["body"],
                correlation_id=r["correlation_id"],
                created_at=r["created_at"],
                read_at=r["read_at"],
            )
            for r in rows
        ]

    async def unread_count(self, org_id: str) -> int:
        """How many unread rows this org has. Drives the console's badge."""
        async with self._db.tenant_session(org_id) as conn:
            value = await conn.fetchval(
                "SELECT count(*) FROM notifications "
                "WHERE org_id = $1 AND read_at IS NULL",
                org_id,
            )
        return int(value or 0)

    async def mark_read(
        self, *, org_id: str, notification_id: UUID, at: datetime | None = None
    ) -> NotificationRow | None:
        """Acknowledge one notification. Idempotent; returns None if not found.

        IDEMPOTENT BY `read_at IS NULL`, not by a blind UPDATE: a second call
        must not move the timestamp, because the first acknowledgement is the
        one that happened. The RETURNING clause is driven off a fresh SELECT so
        an already-read row still comes back (the caller asked to make it read;
        it is read) rather than looking like a 404.

        Tenant-scoped via RLS: a caller cannot acknowledge another org's row
        even with its id in hand — the policy makes the row unmatched, so this
        returns None exactly as it would for an id that does not exist.
        """
        moment = at
        async with self._db.tenant_session(org_id) as conn:
            if moment is None:
                await conn.execute(
                    "UPDATE notifications SET read_at = now() "
                    "WHERE notification_id = $1 AND org_id = $2 AND read_at IS NULL",
                    notification_id,
                    org_id,
                )
            else:
                await conn.execute(
                    "UPDATE notifications SET read_at = $3 "
                    "WHERE notification_id = $1 AND org_id = $2 AND read_at IS NULL",
                    notification_id,
                    org_id,
                    moment,
                )
            row = await conn.fetchrow(
                "SELECT notification_id, org_id, kind, severity, title, body, "
                "correlation_id, created_at, read_at FROM notifications "
                "WHERE notification_id = $1 AND org_id = $2",
                notification_id,
                org_id,
            )
        if row is None:
            return None
        return NotificationRow(
            notification_id=row["notification_id"],
            org_id=row["org_id"],
            kind=row["kind"],
            severity=row["severity"],
            title=row["title"],
            body=row["body"],
            correlation_id=row["correlation_id"],
            created_at=row["created_at"],
            read_at=row["read_at"],
        )
