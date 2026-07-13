"""workflow_run_steps — per-node execution audit trail for the Temporal engine

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-13

Backing table for `WorkflowRepository.record_step` (dal/ports.py), written by the
Temporal `write_run_step` activity (app/orchestrator/temporal/activities.py) —
one row per workflow node execution, capturing its input, output, judge verdict
and failure reason.

Tenant-scoped with RLS. The table/policy/grant shape is copied verbatim from
migration 0006 (deliverables): ENABLE + FORCE ROW LEVEL SECURITY, a single
`tenant_isolation` FOR ALL policy keyed on `current_setting('skylize.org_id')`
for both USING and WITH CHECK, and DML granted to `skylize_app` (the
NOSUPERUSER/NOBYPASSRLS role from migration 0003, so the policy actually binds).
This table is NOT part of the migration-0002 rehydrate carve-out — that read
carve-out exists for the Governance Authority's startup snapshot, and a run-step
trail has no reason to be readable across tenants.

Two deliberate non-choices, both driven by the absence of a caller to derive
them from (nothing in src/ constructs WorkflowActivities yet):

  - `status` is plain TEXT with no CHECK constraint. activities.py only reveals
    two values ('completed', 'failed' — the ones that set completed_at); the
    rest of the engine's vocabulary is unknown. A guessed allow-list would fail
    writes at runtime, which is strictly worse than accepting an unexpected
    string into an audit row.
  - No UNIQUE key over (run_id, step_order). activities.py mints a fresh
    step_id per invocation, so this is an append-only attempt log: Temporal's
    at-least-once activity delivery may append more than one row for the same
    node. That records reality (every attempt is visible) rather than hiding it
    behind an upsert whose natural key we would have to invent.

`run_id` carries no foreign key — there is no workflow_runs table in this schema;
Temporal owns the run lifecycle and the id is its workflow run identifier.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"


def upgrade() -> None:
    # asyncpg executes only one statement per op.execute() call; every DDL
    # statement must be its own call.

    op.execute(sa.text("""
        CREATE TABLE workflow_run_steps (
            step_id       UUID PRIMARY KEY,
            run_id        UUID NOT NULL,
            org_id        TEXT NOT NULL REFERENCES tenants(org_id),
            step_name     TEXT NOT NULL,
            step_order    INT NOT NULL,
            agent_id      TEXT NOT NULL,
            status        TEXT NOT NULL,
            input         JSONB NOT NULL DEFAULT '{}',
            output        JSONB,
            judge_verdict JSONB,
            error_message TEXT,
            retry_count   INT NOT NULL DEFAULT 0,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at  TIMESTAMPTZ
        )
    """))

    # Primary read path: replay one run's steps in execution order.
    op.execute(sa.text(
        "CREATE INDEX idx_workflow_run_steps_org_run "
        "ON workflow_run_steps (org_id, run_id, step_order)"
    ))
    # Secondary: recent activity for an org (dashboard / triage).
    op.execute(sa.text(
        "CREATE INDEX idx_workflow_run_steps_org_created "
        "ON workflow_run_steps (org_id, created_at DESC)"
    ))

    op.execute(sa.text("ALTER TABLE workflow_run_steps ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE workflow_run_steps FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
        CREATE POLICY tenant_isolation ON workflow_run_steps
        FOR ALL
        USING (org_id = current_setting('skylize.org_id', true))
        WITH CHECK (org_id = current_setting('skylize.org_id', true))
    """))

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON workflow_run_steps TO {_APP_ROLE};")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON workflow_run_steps FROM {_APP_ROLE};")
    op.execute("DROP TABLE IF EXISTS workflow_run_steps CASCADE;")
