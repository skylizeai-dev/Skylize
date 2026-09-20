"""workflow_runs — first-class run identity for the LIVE orchestrator path

Revision ID: 0033
Revises: 0032
Create Date: 2026-09-20

WHY THIS TABLE EXISTS. Migration 0010 created ``workflow_run_steps`` and stated
in its own docstring: "run_id carries no foreign key — there is no workflow_runs
table in this schema; Temporal owns the run lifecycle." That premise no longer
holds for the path that actually executes. ``workflow_run_steps`` is written
only by ``app/orchestrator/temporal/activities.py``, whose whole tree is PAUSED
(pyproject.toml) — nothing in bootstrap.py constructs it and nothing schedules
it. The LIVE path is ``Orchestrator.invoke`` (app/orchestrator/orchestrator.py),
reached from ``POST /api/v1/workflows/creative``, and it was execute-and-forget:
a run happened, an audit row was written, and no queryable run row survived.
This table gives the live path a run identity so the console's run-history
screen reads facts instead of a mock.

FOREIGN KEYS — the deliberate choices.

  * ``org_id`` REFERENCES tenants(org_id). Same as every tenant-scoped table in
    this schema, including 0010. A run belongs to an org or it belongs nowhere.

  * ``run_id`` carries NO foreign key TO anything, and nothing here adds one
    FROM ``workflow_run_steps.run_id`` TO this table. That restraint is the
    point. 0010's run_id is a TEMPORAL workflow run identifier minted by an
    engine that is not running; this table's run_id is the LIVE orchestrator's
    ``correlation_id``. They are two different id spaces that happen to share a
    column name. Adding the FK 0010 declined to add would either (a) be
    satisfied vacuously today, since nothing writes steps, and then break the
    moment the Temporal worker is unpaused with its own ids, or (b) force the
    two engines to share an id space that neither was designed to share. A
    backfill-free, engine-agnostic run table is worth more than a constraint
    that encodes a guess about which engine wins.

  * Consequently ``workflow_run_steps`` is NOT joined to this table and NO
    stage-by-stage progress is derivable from it. See "WHAT THIS DOES NOT
    RECORD" below.

run_id IS the correlation_id. ``Orchestrator.invoke`` mints exactly one
``correlation_id`` per invocation and threads it through the governance token,
the LangGraph thread_id, the audit rows and the published business event. Using
it as the primary key here means a run row joins to its audit trail
(``audit_log.correlation_id``) and to its cost rows
(``ai_cost_ledger.correlation_id``, indexed by migration 0029) with no new
identifier and no mapping table. ``correlation_id`` is kept as a separate,
indexed column anyway: the PK is a run's IDENTITY and the column is its
CORRELATION KEY, and if a future engine ever mints a run id that is not the
correlation id, the read path does not change.

STATUS vocabulary is CHECKed to the three values ``WorkflowResult.status``
actually produces — ``running``, ``completed``, ``denied``, ``failed`` — where
``running`` is this table's own addition for the open interval between the start
row and its finish. 0010 deliberately left its status unconstrained because it
had no caller to derive the vocabulary from; here there IS a caller
(orchestrator.py returns exactly ``completed`` | ``denied`` | ``failed``), so
the CHECK is derived from code rather than guessed, and a value outside it is a
bug worth failing on.

WHAT THIS DOES NOT RECORD. There is no stage/step progress here. The live graph
(``build_creative_graph``) does not emit per-node persistence, and this
migration does not invent it: a run has a start, an end, a status and a reason.
A console rendering a stage-by-stage pipeline from this table would be
rendering fiction. ``failure_stage`` is the ONE stage-shaped column, and it is
honest — it is the ``failed_stage`` the graph already puts in its terminal
state, i.e. where a run STOPPED, not how far it got.

MUTABILITY. Unlike ``ai_cost_ledger`` (0012) this table is NOT append-only: a
run is inserted ``running`` and UPDATEd exactly once when it finishes. That is
the lifecycle, not a rewrite of history — the audit trail, which IS append-only
and trigger-protected, remains the evidence of record. UPDATE is therefore
granted; DELETE is revoked, following 0028's reasoning: run history is operator
evidence and the app role has no business erasing it.

RLS is modelled EXACTLY on migration 0028 (and through it on 0014/0012):
ENABLE + FORCE ROW LEVEL SECURITY so even the table owner is a policy subject,
and a single ``tenant_isolation`` FOR ALL policy keyed on
``current_setting('skylize.org_id')`` for both USING and WITH CHECK. The
non-superuser ``skylize_app`` role (migration 0003) is thus a genuine RLS
subject: one org can neither read nor write another org's run history.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"


def upgrade() -> None:
    # asyncpg executes only one statement per op.execute() call; each DDL
    # statement is therefore its own call.

    op.execute(sa.text("""
        CREATE TABLE workflow_runs (
            run_id         UUID PRIMARY KEY,
            org_id         TEXT NOT NULL REFERENCES tenants(org_id),
            workflow_name  TEXT NOT NULL,
            agent_id       TEXT NOT NULL,
            status         TEXT NOT NULL CHECK (status IN ('running', 'completed', 'denied', 'failed')),
            correlation_id UUID NOT NULL,
            started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at    TIMESTAMPTZ,
            failure_stage  TEXT,
            reason         TEXT
        )
    """))

    op.execute(sa.text(
        "COMMENT ON COLUMN workflow_runs.run_id IS "
        "'The live orchestrator run identity. Today it IS the invocation''s "
        "correlation_id; no FK to workflow_run_steps.run_id, which belongs to "
        "the paused Temporal engine''s id space (see 0010).'"
    ))
    op.execute(sa.text(
        "COMMENT ON COLUMN workflow_runs.failure_stage IS "
        "'Where the run STOPPED (the graph''s failed_stage, or an orchestrator "
        "pre-graph stage such as resolve/gate/validate_input). NOT progress: no "
        "stage-by-stage advance is recorded anywhere on the live path.'"
    ))
    op.execute(sa.text(
        "COMMENT ON COLUMN workflow_runs.status IS "
        "'running until finished; then one of completed | denied | failed, the "
        "three values WorkflowResult.status produces (orchestrator.py).'"
    ))

    # Primary read path: one org's run history, newest first (console list).
    op.execute(sa.text(
        "CREATE INDEX idx_workflow_runs_org_started "
        "ON workflow_runs (org_id, started_at DESC)"
    ))
    # Join to the audit trail / ai_cost_ledger by correlation.
    op.execute(sa.text(
        "CREATE INDEX idx_workflow_runs_org_correlation "
        "ON workflow_runs (org_id, correlation_id)"
    ))

    op.execute(sa.text("ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE workflow_runs FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
        CREATE POLICY tenant_isolation ON workflow_runs
        FOR ALL
        USING (org_id = current_setting('skylize.org_id', true))
        WITH CHECK (org_id = current_setting('skylize.org_id', true))
    """))

    # A run is inserted then finished once: SELECT + INSERT + UPDATE. The REVOKE
    # is not redundant — migration 0003's ALTER DEFAULT PRIVILEGES grants
    # skylize_app `arwd` on every new table, so DELETE arrives already granted.
    # Run history is operator evidence; the app role must not erase it (0028).
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON workflow_runs TO {_APP_ROLE};")
    op.execute(f"REVOKE DELETE ON workflow_runs FROM {_APP_ROLE};")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON workflow_runs FROM {_APP_ROLE};")
    op.execute(sa.text("DROP TABLE IF EXISTS workflow_runs CASCADE"))
