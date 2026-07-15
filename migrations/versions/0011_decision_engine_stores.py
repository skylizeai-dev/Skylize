"""decision_processed_events + budget_ledger upsert key — durable engine stores

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-15

Makes the Decision Engine's two side stores durable on the postgres backend
(DECISIONS_PENDING #1):

1. `decision_processed_events` — the idempotency guard behind
   `ProcessedEventStore` (dal/ports.py), written by `PgProcessedEventStore`
   (dal/decision_stores.py). One row per decided key — a consumed `event_id`
   or an `hitl:{decision_id}` resume marker — so at-least-once redelivery
   yields exactly one decision even across an engine restart.

   PRIMARY KEY (org_id, key): the write path is `ON CONFLICT DO NOTHING`, so a
   redelivered event races safely and the first recorded outcome sticks.
   Tenant-scoped with RLS; the policy/grant shape is copied verbatim from
   migration 0006 (same as 0010 did): ENABLE + FORCE ROW LEVEL SECURITY, a
   single `tenant_isolation` FOR ALL policy on `current_setting('skylize.org_id')`,
   DML granted to `skylize_app`. Not part of the migration-0002 rehydrate
   carve-out — nothing needs to read idempotency markers across tenants.

2. A UNIQUE index on `budget_ledger (org_id, scope, period)`. The budget-ceiling
   table itself already exists (migration 0001) and is what
   `PgCapitalRepository.get_ceiling` reads — this migration deliberately does
   NOT add a second budget table; it only adds the natural key 0001 left
   implicit, which `set_ceiling`'s idempotent upsert requires. Safe on any
   existing deployment: nothing in src/ has ever written budget_ledger rows
   (the table was schema-only until now), so no duplicate rows can predate
   the index.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"


def upgrade() -> None:
    # asyncpg executes only one statement per op.execute() call; every DDL
    # statement must be its own call.

    op.execute(sa.text("""
        CREATE TABLE decision_processed_events (
            org_id       TEXT NOT NULL REFERENCES tenants(org_id),
            key          TEXT NOT NULL,
            outcome      TEXT NOT NULL,
            processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (org_id, key)
        )
    """))

    op.execute(sa.text("ALTER TABLE decision_processed_events ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE decision_processed_events FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
        CREATE POLICY tenant_isolation ON decision_processed_events
        FOR ALL
        USING (org_id = current_setting('skylize.org_id', true))
        WITH CHECK (org_id = current_setting('skylize.org_id', true))
    """))

    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON decision_processed_events TO {_APP_ROLE};"
    )

    # Natural key for budget_ledger seeding upserts (PgCapitalRepository.set_ceiling).
    op.execute(sa.text(
        "CREATE UNIQUE INDEX uq_budget_ledger_org_scope_period "
        "ON budget_ledger (org_id, scope, period)"
    ))


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_budget_ledger_org_scope_period;")
    op.execute(f"REVOKE ALL ON decision_processed_events FROM {_APP_ROLE};")
    op.execute("DROP TABLE IF EXISTS decision_processed_events CASCADE;")
