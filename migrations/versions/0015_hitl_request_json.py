"""hitl_queue.request_json — the replayable execution envelope (owner decision K4)

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-28

A dedicated column, deliberately NOT a key inside ``proposal_json`` and NOT
``DecisionProposal.metadata`` (owner decision K4): ``proposal_json`` records
WHAT WAS DECIDED; ``request_json`` records WHAT TO EXECUTE IF APPROVED. The two
have different lifecycles, and a customer payload does not belong in an untyped
metadata dict.

``request_json`` holds a serialized ``HitlReplayEnvelope``
(``skylize.schemas.hitl``) — typed at both ends, never a loose dict. NULL is the
honest value for rows with no replayable execution: every row the OPA-side
``HITLQueueWriter`` produces, and every row enqueued before this migration.

Tenant isolation (owner decision K5, verified before adding this column):
``hitl_queue`` already carries ENABLE + FORCE ROW LEVEL SECURITY and the
``tenant_isolation`` policy from migration 0001 (lines 347-367, table array
includes ``hitl_queue``), policy rewritten with the read-only rehydrate
carve-out in migration 0002. A column addition inherits the table's RLS; no RLS
work is needed here.

Backfill (owner decision K10): rows already enqueued have no ``request_json``
and can never be replayed — approval could not reconstruct the request. They are
moved to the terminal ``expired`` status (an existing CHECK-constraint value)
rather than left pending forever. Count observed in the development database at
migration time: 0 pending / 0 total.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE hitl_queue ADD COLUMN request_json JSONB"))
    op.execute(sa.text(
        "COMMENT ON COLUMN hitl_queue.request_json IS "
        "'Serialized skylize.schemas.hitl.HitlReplayEnvelope: what to execute if "
        "approved (agent_id, validated input, user_id, original correlation_id). "
        "NULL = no replayable execution (OPA-side writer rows, pre-0015 rows).'"
    ))
    # K10: pre-existing pending rows carry no replayable request — a human
    # approval could never execute them, so they must not sit in the queue
    # looking actionable. `expired` is an existing status vocabulary value.
    op.execute(sa.text(
        "UPDATE hitl_queue SET status='expired' "
        "WHERE request_json IS NULL AND status='pending'"
    ))


def downgrade() -> None:
    # The K10 status backfill is not reverted: rows expired by 0015 were
    # unreplayable before it and remain unreplayable after a downgrade.
    op.execute(sa.text("ALTER TABLE hitl_queue DROP COLUMN request_json"))
