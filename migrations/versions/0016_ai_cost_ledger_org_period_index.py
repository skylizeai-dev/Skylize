"""Covering index for the org-wide period aggregate on ai_cost_ledger

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-30

WHY: ``CostLedgerDAL.org_period_total_micros`` runs on the HOT PATH of every
single LLM generation — the org spend-ceiling gate reads period-to-date spend
before any provider egress (adapters/llm/spend_ceiling.py). Its predicate is
``WHERE billing_period = $1`` with the org supplied only by the RLS policy
(``org_id = current_setting('skylize.org_id', true)``), and it SUMs
``cost_micros``.

MEASURED, not guessed. PostgreSQL 16.14, real ai_cost_ledger, EXPLAIN (ANALYZE,
BUFFERS) as the non-superuser ``skylize_app`` role with RLS active, inside a
transaction with ``skylize.org_id`` set — exactly how ``tenant_session`` issues
it.

  Fixture A — 236,000 rows total, 20,000 in the measured (org, period):
    Aggregate (actual time=7.390..7.393)
      Bitmap Heap Scan  Heap Blocks: exact=572   Buffers: shared hit=608
        Bitmap Index Scan on idx_ai_cost_ledger_reconcile
          Index Cond: ((org_id = current_setting(...)) AND (billing_period = ...))
    -> the EXISTING (org_id, provider, billing_period) index IS used, and
       billing_period IS an index condition despite `provider` sitting unmatched
       between them: a bitmap index scan applies non-contiguous conditions. At
       this size the existing index is fine.

  Fixture B — 416,000 rows total, 200,000 in the measured (org, period):
    Finalize Aggregate (actual time=75.947..83.632)
      Gather  Workers Launched: 2
        Parallel Seq Scan on ai_cost_ledger
          Filter: ((billing_period = ...) AND (org_id = current_setting(...)))
          Rows Removed by Filter: 72000
      Buffers: shared hit=10270 read=1313        Execution Time: 83.724 ms
    -> the planner ABANDONS the index and reads the WHOLE TABLE, across every
       tenant, on the hot path. RLS's current_setting() is opaque to the planner,
       so it cannot know the org's selectivity, and once the match set is a large
       fraction of the table a seq scan wins on cost. This is the degradation the
       review predicted, though the mechanism is not `provider`'s position in the
       key — it is that the summed column lives only in the heap.

  Fixture B with THIS index:
    Aggregate (actual time=72.556..72.559)
      Index Only Scan using ...  Heap Fetches: 0  Buffers: shared hit=1209
    -> 11,583 buffers -> 1,209 (9.6x fewer pages touched), no parallel workers,
       and no other tenant's heap pages are read at all.

SHAPE: ``INCLUDE (cost_micros)`` rather than a fourth key column — the value is
only summed, never used as a search key, so carrying it as a payload keeps the
key narrow while still allowing ``Heap Fetches: 0``.

ALTERNATIVE TESTED AND REJECTED: widening the existing
``idx_ai_cost_ledger_reconcile`` to ``(org_id, provider, billing_period) INCLUDE
(cost_micros)`` also yields an index-only scan (1,575 buffers) and additionally
upgrades the provider-scoped ``period_total_micros`` to index-only. It was not
chosen because it requires DROP + CREATE of an index that serves a live query,
which an additive migration does not. Consolidating the two is a reasonable
follow-up; it is an owner call, not a silent rewrite here.

COST: ~20 MB at 416,000 rows (heap 90 MB), maintained on every INSERT. That is
one index entry per provider call — the same frequency as the read it serves,
where the read without it is O(whole table) and with it is O(this org's rows in
this period).

No RLS work: an index inherits the table's policy. No data is written or
changed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "idx_ai_cost_ledger_org_period"


def upgrade() -> None:
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS {_INDEX_NAME} "
        "ON ai_cost_ledger (org_id, billing_period) INCLUDE (cost_micros)"
    ))


def downgrade() -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {_INDEX_NAME}"))
