"""notifications — the console's notification feed (org-scoped, RLS)

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-20

WHY THIS TABLE EXISTS. Before it, the only notification-adjacent mechanism in
the repo was `app/notifications/slack.py` — one message kind (HITL approval
requests), posted to ONE hardcoded platform-level Slack channel, post-only, with
no read model and no persistence. The console's Notifications screen therefore
had nothing to read. This table is that read model: a durable, per-org,
tenant-isolated record of the noteworthy things the system already knows
happened, which the console can list and mark read.

ORG-SCOPED, NOT PER-USER — the deliberate choice, and the reason:

  * The events that produce a row are ORG facts, not personal ones. A HITL
    approval request is addressed to *whoever in the org may approve it*, which
    is a role (owner/admin), not a named person. The `hitl_queue` row it mirrors
    carries no addressee column either: `HitlQueueService.approve` takes
    `reviewed_by` at VERDICT time, which is exactly the point — the reviewer is
    not known when the escalation is raised.
  * A per-user column would therefore have to be filled with a guess. Writing a
    principal id nobody chose, and then hiding the notification from every other
    owner because of that guess, would mean an approval request silently
    invisible to the person who would have actioned it. That is a governance
    failure, not a UX preference.
  * The read route is already `require_any_role_or_user("owner", "admin")`, the
    same gate every other console read uses. Org scope and that gate agree: the
    people who can see the feed are the people the feed is for.
  * `read_at` is consequently an ORG-LEVEL fact: "somebody with access has
    acknowledged this", not "this individual has". That is honest about what the
    single column can prove, and the column is named for the state, not for a
    person.

  If per-user delivery is ever needed, the migration path is additive — a
  nullable `principal_id` where NULL keeps today's meaning (org-wide) — so this
  choice does not foreclose the narrower one. It just refuses to fabricate the
  narrower one today.

`correlation_id` is the link back to the originating governed action. It is the
SAME correlation id `audit_log`, `ai_cost_ledger` and the decision events carry,
so a notification is traceable to the run that caused it rather than being a
free-floating string. Nullable, because a producer that genuinely has no
correlation must be able to say so instead of inventing one. NOT a foreign key:
`audit_log` is keyed on `event_id`, correlation is a grouping key there, and a
notification must never be blocked or cascade-deleted by the audit trail.

MUTABLE in exactly one column. `read_at` flips NULL -> timestamp and is the only
UPDATE the app role can usefully perform; there is no append-only trigger because
acknowledging a notification is not an audit fact. Everything that IS an audit
fact already has a row in `audit_log` — this table is a convenience surface over
those facts, never a substitute for them.

RLS is modelled EXACTLY on org_autonomy_mode (migration 0028) and through it on
org_spend_ceiling (0014) / ai_cost_ledger (0012:178-185): ENABLE + FORCE ROW
LEVEL SECURITY so even the table owner is a policy subject, and a single
`tenant_isolation` FOR ALL policy on `current_setting('skylize.org_id')`.

Seed: the table is created EMPTY, and deliberately so. A fresh environment has
had no HITL escalation, so it has no notifications. Seeding demo rows would make
the screen look populated while asserting things that never happened.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"


def upgrade() -> None:
    # asyncpg executes only one statement per op.execute() call; each DDL
    # statement is therefore its own call.

    # ------------------------------------------------------------------
    # notifications — one row per noteworthy org event. Org-scoped only.
    #
    # `kind` and `severity` are CHECK-constrained rather than free text, for the
    # same reason migration 0028 constrains autonomy_mode: the console renders
    # per-kind and per-severity, so a value outside the set would render as
    # nothing. The CHECK is what stops a sixth value entering through raw SQL
    # when some future caller skips the DAL.
    # ------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE TABLE notifications (
            notification_id UUID PRIMARY KEY,
            org_id          TEXT NOT NULL REFERENCES tenants(org_id),
            kind            TEXT NOT NULL CHECK (kind IN ('hitl.approval_requested', 'governance.action_denied')),
            severity        TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
            title           TEXT NOT NULL,
            body            TEXT NOT NULL,
            correlation_id  UUID,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            read_at         TIMESTAMPTZ
        )
    """))

    op.execute(sa.text(
        "COMMENT ON COLUMN notifications.correlation_id IS "
        "'The correlation id of the governed action that produced this "
        "notification - the SAME id audit_log, ai_cost_ledger and the decision "
        "events carry. Nullable so a producer with no correlation can say so "
        "rather than invent one. Deliberately NOT a foreign key: a notification "
        "must never be blocked by, or cascade-deleted with, the audit trail.'"
    ))

    op.execute(sa.text(
        "COMMENT ON COLUMN notifications.read_at IS "
        "'When SOMEBODY with console access acknowledged this row. The table is "
        "org-scoped, so this is an org-level fact, not a per-person one - see "
        "the migration docstring for why per-user delivery was refused rather "
        "than guessed.'"
    ))

    # The list read is "this org, newest first, optionally unread only". A
    # descending (org_id, created_at) index serves both the filtered and the
    # unfiltered form: leading equality on org_id, reverse range on created_at.
    # No separate partial index for unread — an org's unread set is small and is
    # a filter on top of the same ordering, not a different ordering.
    op.execute(sa.text(
        "CREATE INDEX notifications_org_created_idx "
        "ON notifications (org_id, created_at DESC)"
    ))

    # ------------------------------------------------------------------
    # Row-level security — modelled EXACTLY on org_autonomy_mode (0028).
    # ------------------------------------------------------------------
    op.execute(sa.text("ALTER TABLE notifications ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE notifications FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
        CREATE POLICY tenant_isolation ON notifications
        FOR ALL
        USING (org_id = current_setting('skylize.org_id', true))
        WITH CHECK (org_id = current_setting('skylize.org_id', true))
    """))

    # ------------------------------------------------------------------
    # Grants for the non-superuser runtime role (subject to RLS).
    # SELECT + INSERT + UPDATE: the producer inserts, the console reads, and
    # mark-read is the one UPDATE.
    #
    # The REVOKE is not redundant. Migration 0003 set ALTER DEFAULT PRIVILEGES
    # granting skylize_app `arwd` on every table created afterwards, so this
    # table arrives with DELETE already granted. Without the REVOKE a tenant
    # could erase the record that it was told an agent wanted to act — which is
    # exactly the evidence a governance console exists to keep in view.
    # Dismissing a notification is `read_at`, not a delete.
    # ------------------------------------------------------------------
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON notifications TO {_APP_ROLE};")
    op.execute(f"REVOKE DELETE ON notifications FROM {_APP_ROLE};")

    # ------------------------------------------------------------------
    # Seed: intentionally EMPTY. See the docstring.
    # ------------------------------------------------------------------
    # (no INSERTs)


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON notifications FROM {_APP_ROLE};")
    op.execute(sa.text("DROP TABLE IF EXISTS notifications CASCADE"))
