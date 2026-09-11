"""hitl_queue.resumption_json — the frozen model turn a human reviewed

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-09

Design: docs/architecture/hitl_approval_resumption_design.md sections 3.2, 6
(owner decisions D1/D2/D4 + drain strategy, signed off 2026-09-09).

A DEDICATED COLUMN, NOT A FIELD INSIDE ``request_json``. This is migration
0015's own rule applied a second time. 0015 put ``request_json`` in its own
column rather than "a key inside ``proposal_json``" because "the two have
different lifecycles" (0015_hitl_request_json.py:7-11). The same distinction
holds here, and more sharply:

  * ``request_json``    — the frozen replay identity: agent_id, validated input,
                          user_id, correlation. Small. "Written ONCE at enqueue
                          and never rewritten" (app/hitl/service.py:23), which
                          ``_terminate_failed`` reasons from.
  * ``resumption_json`` — the conversation prefix the reviewed turn was sampled
                          from, plus the pending ``tool_use`` block ids. Larger,
                          more sensitive, and meaningful only while the row is
                          still claimable.

Keeping them apart is what lets this change preserve the never-rewritten
invariant on ``request_json`` completely untouched.

CONTENT AND RETENTION (owner decision D2). The column holds a serialized
``skylize.schemas.hitl.HitlResumptionPoint`` and nothing else: the message
prefix INCLUDING the assistant message carrying the reviewed ``tool_use``
block(s), those blocks' ids, the loop iteration, and the running token total.
No governance token, no compiled scope set, no ``authority_fingerprint``, no
``org_id`` — the resumed run re-mints and recompiles authority at approval time
(schemas/hitl.py). The action is frozen; the authority is not.

Retention is the existing HITL discipline, not a new one: ``expires_at`` (48h,
app/agents/execution.py:78) already bounds how long the row is claimable, and
the conditional claim already refuses an expired row
(dal/hitl.py:169-179). An expired snapshot is therefore REJECTED (HTTP 410),
never downgraded to a re-sample of a run nobody can still review.

TENANT ISOLATION. ``hitl_queue`` already carries ENABLE + FORCE ROW LEVEL
SECURITY and the ``tenant_isolation`` policy from migration 0001 (lines
347-367), rewritten with the read-only rehydrate carve-out in 0002. A column
addition inherits the table's RLS; no RLS work is needed here.

NO BACKFILL. NULL is the correct and honest value for every existing row: they
carry no resumption state, and ``resumption_json IS NULL`` is a fully supported
state meaning "approval re-runs the agent from ``input``", which is exactly
today's semantics. Unlike 0015's K10 backfill, no row becomes unactionable
because of this migration, so no row is expired by it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE hitl_queue ADD COLUMN resumption_json JSONB"))
    op.execute(sa.text(
        "COMMENT ON COLUMN hitl_queue.resumption_json IS "
        "'Serialized skylize.schemas.hitl.HitlResumptionPoint: the exact model "
        "turn a human reviewed (message prefix + pending tool_use ids + loop "
        "iteration + running token total), replayed VERBATIM on approval. "
        "NULL = request-level ticket, approval re-runs the agent from "
        "request_json.input (pre-0027 rows, stage-2.5 defers, OPA-side rows).'"
    ))


def downgrade() -> None:
    # GRACEFUL, and deliberately so: dropping the column loses the snapshot for
    # any row still pending, which downgrades those rows from "resume this exact
    # call" to "re-run this agent on this input". That is a supported state, not
    # an error — the approval path branches on `resumption is None` and takes
    # today's semantics — so a downgrade degrades the guarantee without breaking
    # any queued row. Stated here following 0015_hitl_request_json.py:62-65's
    # practice of saying what a downgrade does and does not revert.
    op.execute(sa.text("ALTER TABLE hitl_queue DROP COLUMN resumption_json"))
