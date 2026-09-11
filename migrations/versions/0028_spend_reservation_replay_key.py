"""spend_reservation.replay_key + result_snapshot — replay identity for a spend

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-11

Design: docs/architecture/spend_reservation_replay_semantics.md sections 3A and
3C. Depends on 0027: the replay identity this column stores is
``ToolContext.replay_key()`` (src/skylize/tools/base.py:88), which is only
non-None on a turn resumed from ``hitl_queue.resumption_json`` — the column 0027
added. Without 0027 there is no stable per-call identity to key on, which is
exactly why this migration could not be written before it.

WHY A PARTIAL UNIQUE INDEX, NOT A TABLE CONSTRAINT. The existing
``UNIQUE (org_id, idempotency_key)`` is unconditional: it spans all four states,
so once a key has been used it can never back a second reservation, in any
state. That is wrong for replay. The intended rule is state-differentiated:

  * ``held``      — a replay must NOT re-execute and must NOT place a second hold;
  * ``committed`` — likewise, and the recorded result is returned instead;
  * ``released``  — the key is FREE, because a released hold does not mean the
                    side effect did not happen, only that this attempt stopped;
  * ``expired``   — likewise free: a swept hold means a worker died mid-call.

Settling a released or expired replay to zero, or reusing its settled row, would
under-count a spend that subsequently succeeds — worse than the double-count
this work exists to fix (section 6).

Postgres constraints cannot carry a ``WHERE``, so "at most one LIVE-or-SETTLED
reservation per replay key" is only expressible as a partial unique INDEX. That
is not a workaround; the same table already uses the pattern for
``spend_reservation_sweep ... WHERE (state = 'held')`` (migration 0019), and
this mirrors it.

``idempotency_key`` is left exactly as it was — same meaning, same unconditional
index. The two keys answer different questions: ``idempotency_key`` is "is this
the same request", ``replay_key`` is "is this the same logical tool call across
an approval retry". Collapsing them would put the state-differentiated rule onto
the key that ordinary non-replay traffic also uses.

RESULT SNAPSHOT (section 3C). A ``committed`` replay must return the ORIGINAL
result rather than re-executing, and nothing stored it: the ledger keeps
``committed_minor`` (the amount) but never the tool's output, and the audit row
is not addressable for this purpose because ``HitlQueueService.approve`` mints a
fresh correlation id on every approval attempt.

It is a column HERE, on the row whose ``state`` already decides the replay, and
deliberately NOT a second copy of 0027's ``resumption_json``. Those two store
opposite halves of a call and have different lifetimes:

  * ``resumption_json`` — the PRE-execution action (message prefix, pending
    tool_use ids), bounded by the ticket's 48h ``expires_at``, and REJECTED once
    expired (dal/hitl.py).
  * ``result_snapshot`` — the POST-execution result, which must outlive the
    ticket entirely: the reservation row is the audit record of money that
    moved, and a replay arriving after the ticket expired still must not
    re-execute a committed spend.

Storing it on the reservation also means it is deleted with the reservation and
never separately, so a result can never outlive the money record it describes.

TENANT ISOLATION. ``spend_reservation`` carries ENABLE + FORCE ROW LEVEL
SECURITY and the ``tenant_isolation`` policy from migration 0019. Column
additions inherit the table's RLS, so no RLS work is needed here — the same
reasoning 0027 records for ``hitl_queue``. The partial index is keyed on
``(org_id, replay_key)`` with ``org_id`` FIRST so uniqueness can never collide
across tenants.

NO BACKFILL. NULL is the correct value for every existing row: they predate
replay keying and carry no replay identity. ``replay_key IS NULL`` is a fully
supported state meaning "this reservation is not a replay of anything", which is
what every ordinary non-HITL tool call will keep writing — the partial index
excludes NULLs explicitly so those rows never contend with each other.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LIVE_REPLAY_INDEX = "spend_reservation_replay_live"


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE spend_reservation ADD COLUMN replay_key UUID"))
    op.execute(sa.text("ALTER TABLE spend_reservation ADD COLUMN result_snapshot JSONB"))

    op.execute(sa.text(
        "COMMENT ON COLUMN spend_reservation.replay_key IS "
        "'ToolContext.replay_key(): uuid5(hitl_id, tool_use_id) for a tool call "
        "dispatched from a human-approved turn replayed verbatim from storage. "
        "NULL on every ordinary (non-resumed) call, which is most of them.'"
    ))
    op.execute(sa.text(
        "COMMENT ON COLUMN spend_reservation.result_snapshot IS "
        "'The tool output this reservation paid for, as JSON, written at commit. "
        "Read INSTEAD of re-executing when a replay finds this row committed. "
        "NULL until settled, and on reservations that carry no replay_key.'"
    ))

    # At most one live-or-settled reservation per replay key, per tenant.
    # `held` and `committed` block a replay; `released` and `expired` free the
    # key so a genuine re-attempt can place a real hold and spend for real.
    op.execute(sa.text(
        f"CREATE UNIQUE INDEX {_LIVE_REPLAY_INDEX} "
        "ON spend_reservation (org_id, replay_key) "
        "WHERE replay_key IS NOT NULL AND state IN ('held', 'committed')"
    ))


def downgrade() -> None:
    # Dropping these loses the replay identity and the recorded results for any
    # reservation still carrying them, which downgrades a `committed` replay from
    # "return the original result" back to "re-execute". That is the pre-0028
    # behaviour and the defect this migration exists to fix, so a downgrade
    # reintroduces a known double-count rather than merely losing a convenience.
    # Stated plainly here following 0027's practice of saying what a downgrade
    # does and does not revert.
    op.execute(sa.text(f"DROP INDEX IF EXISTS {_LIVE_REPLAY_INDEX}"))
    op.execute(sa.text("ALTER TABLE spend_reservation DROP COLUMN result_snapshot"))
    op.execute(sa.text("ALTER TABLE spend_reservation DROP COLUMN replay_key"))
