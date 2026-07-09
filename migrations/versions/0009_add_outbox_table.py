"""Add decision_outbox table for transactional outbox pattern

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-24

Transactional outbox for decision events. Rows are written in the same DB
transaction as the decision record; a separate relay process reads unpublished
rows and XADDs them to Redis streams, then stamps published_at.

outbox_row_id format: {unix_ms}-{last_4_digits_of_outbox_uuid_int}

  Redis stream IDs require the format "{milliseconds}-{sequence}". Using the
  last 4 decimal digits of the UUID integer as the sequence component gives a
  monotonically-increasing, collision-resistant composite that satisfies Redis
  without a separate sequence counter. Compute at INSERT time (before the row
  is written), store in outbox_row_id, and pass verbatim as the XADD ID.

  Example: uuid=550e8400-e29b-41d4-a716-446655440000  → int last 4 = 0000
           unix_ms=1719187200000                       → row_id = 1719187200000-0000

RLS policy uses the same skylize.org_id session variable convention as all
other tenant-scoped tables (see migration 0001 + docs/architecture/05_security_architecture.md).
"""

from __future__ import annotations

from alembic import op

revision: str = "0009"
down_revision: str = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET search_path TO public")

    op.execute(
        """
        CREATE TABLE decision_outbox (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       TEXT NOT NULL,
            stream_key      TEXT NOT NULL,
            event_type      TEXT NOT NULL,
            payload         JSONB NOT NULL,
            outbox_row_id   TEXT NOT NULL UNIQUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            published_at    TIMESTAMPTZ,
            failed_at       TIMESTAMPTZ,
            retry_count     SMALLINT NOT NULL DEFAULT 0
        );
        """
    )

    op.execute(
        """
        CREATE INDEX idx_outbox_unpublished
            ON decision_outbox (tenant_id, created_at)
            WHERE published_at IS NULL AND failed_at IS NULL;
        """
    )

    op.execute(
        """
        CREATE INDEX idx_outbox_published_at
            ON decision_outbox (published_at)
            WHERE published_at IS NOT NULL;
        """
    )

    op.execute("ALTER TABLE decision_outbox ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE decision_outbox FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY outbox_tenant_isolation ON decision_outbox
            FOR ALL
            USING (tenant_id = current_setting('skylize.org_id', true))
            WITH CHECK (tenant_id = current_setting('skylize.org_id', true));
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS outbox_tenant_isolation ON decision_outbox;"
    )
    op.execute("DROP INDEX IF EXISTS idx_outbox_published_at;")
    op.execute("DROP INDEX IF EXISTS idx_outbox_unpublished;")
    op.execute("DROP TABLE IF EXISTS decision_outbox;")
