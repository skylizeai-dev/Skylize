"""gcp_containment_claims — durable, cross-replica lock for containment proposals

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-05

WHY THIS EXISTS. 928c3d5 wired a spend-ceiling breach to auto-propose a GCP
containment (`app/gcp/trigger.py`), and guarded against flooding the HITL queue
with a per-org cooldown held as an IN-PROCESS Python dict
(`SpendCeilingContainmentTrigger._last_proposed`). That dict is invisible to any
other replica: two app instances that each observe a breach for the same org
within the cooldown window can each independently decide "no proposal is
pending" and each create one. Bounded (two queue rows a human can read and
reject), but not correct under horizontal scaling. This table closes that gap
with a row Postgres itself arbitrates, replacing the in-process timer.

WHY A NEW TABLE AND NOT A COLUMN ON `hitl_queue` OR `gcp_wif_connections`.
Neither existing table can host this without expanding this pass's blast
radius past the one thing it needs to fix:

  * `hitl_queue` is written by `HitlQueueRepository.enqueue`
    (dal/hitl.py) — the SHARED escalation path every governed agent uses, not
    only the GCP executor. Enforcing "one pending containment per org" as a
    constraint ON that table would require either a partial unique index whose
    violation must be caught somewhere inside `enqueue()`'s generic INSERT (a
    change to code every agent's deferred flow depends on, for one caller's
    concern), or teaching `app/gcp/trigger.py` to catch a raw Postgres
    exception — which the import-linter contract "Application logic contains
    no SQL (depends on dal ports only)" forbids for anything under `skylize.app`.
    A dedicated table keeps the fix entirely inside GCP-specific code.
  * `gcp_wif_connections` (migration 0024, the FROZEN foundation) is explicitly
    out of bounds for this pass — see that migration's own docstring and
    23d339c. This table does not touch it, does not add a column to it, and
    carries no FK from it.

WHY A ROW-LEVEL CLAIM, NOT A POSTGRES ADVISORY LOCK. An advisory lock only
serializes callers holding the SAME connection/transaction for its full
duration. The critical section here spans a full `AgentExecutionService.execute()`
call — an LLM round trip, a tool dispatch, a `hitl_queue` write — which runs on
its OWN connection deep inside code this migration's caller does not control.
Holding one physical connection (and a lock) open across that would mean an
external network call keeps a database connection pinned, and "did the lock
survive a dropped connection" becomes a second failure mode to reason about. A
committed row, checked with an ordinary `INSERT ... ON CONFLICT`, needs neither.

THE SHAPE OF THE GUARANTEE. One row per `(org_id, label)`, the same identity
cardinality `oauth_credentials` and `gcp_wif_connections` already use. Claiming
is `INSERT ... ON CONFLICT (org_id, label) DO UPDATE ... WHERE <the existing
claim is stale>`; Postgres serializes concurrent conflicts on the same key
itself, so of two replicas racing to claim the same org, exactly one gets a
returned row and proceeds — the same "the guarantee lives in SQL, not in
Python" reasoning `app/principal/spend.py`'s `_RESERVE_SQL` already states for
`spend_reservation`'s `UNIQUE (org_id, idempotency_key)`.

A claim is STALE, and therefore reclaimable, in exactly two cases:
  * `hitl_id IS NOT NULL` and that ticket is no longer `'pending'` in
    `hitl_queue` — a human already acted, so the row this claim was guarding no
    longer needs guarding. This is the HITL-QUEUE-BACKED half of the design:
    the claim's lifetime is bounded by the real decision's lifetime, not by a
    fixed timer, so a containment resolved in ten seconds does not block a
    genuinely new breach for the rest of a five-minute window.
  * `hitl_id IS NULL` and the claim is older than the crash-recovery timeout
    (`app/gcp/trigger.py::CLAIM_CRASH_TIMEOUT`) — the claimant died between
    reserving the slot and recording the ticket it produced, so nothing will
    ever complete the CASE-above condition for it. This is a backstop, not the
    steady-state mechanism.

`hitl_id` is NOT covered by a foreign key. `hitl_queue` rows are never deleted
in this codebase (verified: no `DELETE FROM hitl_queue` exists anywhere in
`src/`), so nothing here depends on that remaining true, but a claim that
briefly refers to a ticket the writer's transaction has not yet committed must
never be blocked by a constraint checked at statement time — `record_hitl_id`
runs strictly after the ticket exists, so this is a belt not needed, and it is
left off deliberately rather than added narrowly.

RLS mirrors 0024 exactly (ENABLE + FORCE, `tenant_isolation`, `skylize_app`
grant) — the identical policy body copied verbatim, no bypass or carve-out.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"
_TABLE = "gcp_containment_claims"


def upgrade() -> None:
    op.execute(sa.text(f"""
        CREATE TABLE {_TABLE} (
            org_id      TEXT NOT NULL REFERENCES tenants(org_id),
            label       TEXT NOT NULL DEFAULT '',
            hitl_id     UUID,
            claimed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (org_id, label)
        )
    """))

    op.execute(sa.text(
        f"COMMENT ON TABLE {_TABLE} IS "
        "'A durable mutual-exclusion slot per (org_id, label), claimed before "
        "proposing a GCP containment so two replicas racing the same ceiling "
        "breach cannot both queue one. hitl_id NULL = claim reserved, execute() "
        "still in flight; hitl_id set = the real pending ticket this claim "
        "guards. See migration 0025''s docstring for the staleness rule that "
        "makes a row reclaimable.'"
    ))

    op.execute(sa.text(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"""
        CREATE POLICY tenant_isolation ON {_TABLE}
        FOR ALL
        USING (org_id = current_setting('skylize.org_id', true))
        WITH CHECK (org_id = current_setting('skylize.org_id', true))
    """))
    op.execute(sa.text(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO {_APP_ROLE}"
    ))


def downgrade() -> None:
    op.execute(sa.text(f"REVOKE ALL ON {_TABLE} FROM {_APP_ROLE}"))
    op.execute(sa.text(f"DROP TABLE IF EXISTS {_TABLE} CASCADE"))
