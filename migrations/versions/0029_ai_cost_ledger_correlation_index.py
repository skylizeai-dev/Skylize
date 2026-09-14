"""ai_cost_ledger — index (org_id, correlation_id) for the per-run cost read

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-14

Why: ``PgCostLedger.run_total_micros`` (dal/cost_ledger.py) sums one run's
``cost_micros`` keyed on ``correlation_id``, and every completed autonomous run
calls it once to put a truthful ``cost_minor`` on its ``work_journal`` row
(app/autonomy/runner.py). No index covered that column -- migration 0012 indexes
idem / reconcile / org_time, 0016 adds org+period -- so the read was a sequential
scan of the whole ledger. One seq scan per run is negligible at the pilot's one
agent; it is not negligible once the fleet is scheduled, which is the point this
lands ahead of. The dal docstring flagged exactly this.

Shape -- (org_id, correlation_id) INCLUDE (cost_micros), and each part earns its
place:

  * ``org_id`` LEADS even though the SQL never names it. The query runs inside
    ``tenant_session``, so the RLS policy on this table
    (``org_id = current_setting('skylize.org_id', true)``, migration 0012) is
    ANDed into the predicate before planning. The effective search is therefore
    (org_id, correlation_id), and a leading org_id also matches every other
    index on this table -- idx_ai_cost_ledger_org_time, _org_period, _reconcile
    and uq_ai_cost_ledger_idem all lead with it.
  * ``INCLUDE (cost_micros)`` carries the only summed column in the leaf, so the
    SUM is an index-only scan that never touches the heap. The same trick
    migration 0016 uses for idx_ai_cost_ledger_org_period.

NOT created CONCURRENTLY, deliberately. CREATE INDEX takes a SHARE lock that
blocks writers for the duration of the build, so on a large table CONCURRENTLY
would be the right call; on this one it is not warranted and is actively awkward:

  * the table is EMPTY in dev (0 rows, 48 kB total relation size as of this
    commit), so the build is effectively instantaneous;
  * CREATE INDEX CONCURRENTLY cannot run inside a transaction block, and Alembic
    runs each migration in one. It would need an explicit autocommit block, and
    no migration in this repo does that -- 0029 is not the place to introduce the
    pattern.

If ai_cost_ledger is ever large at deploy time, revisit this: the fix is an
autocommit block plus CONCURRENTLY, not a different index shape.

Append-only (ADR-0006) is unaffected. An index changes no row and the
``ai_cost_ledger_append_only`` trigger is untouched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "idx_ai_cost_ledger_org_correlation"


def upgrade() -> None:
    # asyncpg executes only one statement per op.execute() call.
    op.execute(sa.text(f"""
        CREATE INDEX {_INDEX}
        ON ai_cost_ledger (org_id, correlation_id)
        INCLUDE (cost_micros)
    """))

    op.execute(sa.text(
        f"COMMENT ON INDEX {_INDEX} IS "
        "'Serves PgCostLedger.run_total_micros: SUM(cost_micros) for one run, "
        "keyed on correlation_id under an RLS org_id predicate. INCLUDE makes "
        "the SUM an index-only scan.'"
    ))


def downgrade() -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {_INDEX}"))
