"""notifications.org_id FK -> ON DELETE CASCADE

Revision ID: 0037
Revises: 0036
Create Date: 2026-09-27

WHY. Migration 0034 created `notifications` with
`org_id TEXT NOT NULL REFERENCES tenants(org_id)` and no ON DELETE action,
so Postgres auto-named the constraint `notifications_org_id_fkey` and left it
RESTRICT. Deleting a tenant that still has any notification row therefore
raises ForeignKeyViolationError. The integration suite hit this on every HITL
test that produces a notification (both real producers -- the approval request
and the governance refusal -- run through the HITL path), because the teardown
deletes `tenants` and `notifications` was never added to the per-test cleanup
list. That failure was masked until the greenlet dependency gap let the
integration job run its tests at all.

A cascade is the durable fix: `notifications` is a tenant-owned convenience
feed (0034's docstring: "NOT AN AUDIT TRAIL ... a convenience surface"), so
when its tenant is deleted the rows should go with it, and no future teardown
list omission can reresurrect this violation.

NOTE for review: no other table in this schema uses ON DELETE CASCADE on its
`tenants(org_id)` FK -- they rely on explicit per-test teardown ordering
instead. This makes `notifications` the first cascading tenant FK. That is
intentional here (a convenience feed is exactly the kind of dependent data a
tenant delete should take with it), but it is a deliberate divergence from the
existing convention, not an oversight. The companion conftest change adds
`notifications` to the teardown list regardless, so the fix holds under either
reading.

Nothing has an FK TO `notifications` (verified: no `REFERENCES notifications`
anywhere in migrations/), so this cascade cannot propagate beyond these rows.

The auto-generated constraint name is dropped and re-added rather than altered,
because Postgres has no ALTER CONSTRAINT for the ON DELETE action -- it must be
dropped and recreated. asyncpg executes one statement per op.execute() call,
so the drop and the add are separate calls.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE notifications DROP CONSTRAINT notifications_org_id_fkey"
    ))
    op.execute(sa.text(
        "ALTER TABLE notifications ADD CONSTRAINT notifications_org_id_fkey "
        "FOREIGN KEY (org_id) REFERENCES tenants(org_id) ON DELETE CASCADE"
    ))


def downgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE notifications DROP CONSTRAINT notifications_org_id_fkey"
    ))
    op.execute(sa.text(
        "ALTER TABLE notifications ADD CONSTRAINT notifications_org_id_fkey "
        "FOREIGN KEY (org_id) REFERENCES tenants(org_id)"
    ))
