"""asyncpg implementations of CapitalRepository and ProcessedEventStore.

These make the Decision Engine's two side stores durable on the postgres
backend (the memory backend keeps the in-memory fakes):

  - `PgCapitalRepository` reads budget ceilings from the EXISTING
    `budget_ledger` table (migration 0001) — the schema already owns the
    budget-ceiling domain, so no parallel table is introduced. `get_ceiling`
    follows the established read convention (see decision_engine/capital_dal.py):
    the most recent `period` row for an (org, scope) wins. `set_ceiling` is a
    seeding/ops helper (not on the port — the evaluator only reads); its upsert
    relies on the UNIQUE (org_id, scope, period) index added in migration 0011.

  - `PgProcessedEventStore` backs the engine's idempotency guard with the
    `decision_processed_events` table (migration 0011). `mark_processed` is
    `ON CONFLICT DO NOTHING`, so at-least-once redelivery racing a concurrent
    marker never errors — the first outcome recorded for a key sticks.

Every query runs inside `Database.tenant_session(org_id)`, so the RLS
`tenant_isolation` policies apply — tenant isolation holds at the data layer
regardless of upstream checks.
"""

from __future__ import annotations

from .connection import Database
from .ports import BudgetCeiling


class PgCapitalRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_ceiling(self, org_id: str, scope: str) -> BudgetCeiling | None:
        async with self._db.tenant_session(org_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT ceiling, committed
                FROM budget_ledger
                WHERE scope = $1
                ORDER BY period DESC
                LIMIT 1
                """,
                scope,
            )
        if row is None:
            return None
        return BudgetCeiling(
            org_id=org_id,
            scope=scope,
            ceiling_minor_units=row["ceiling"],
            committed_minor_units=row["committed"],
        )

    async def set_ceiling(self, ceiling: BudgetCeiling, *, period: str = "default") -> None:
        """Seed/update one ledger row (bootstrap + tests; the port only reads)."""
        async with self._db.tenant_session(ceiling.org_id) as conn:
            await conn.execute(
                """
                INSERT INTO budget_ledger (org_id, scope, ceiling, committed, period)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (org_id, scope, period) DO UPDATE
                SET ceiling = EXCLUDED.ceiling,
                    committed = EXCLUDED.committed,
                    updated_at = now()
                """,
                ceiling.org_id,
                ceiling.scope,
                ceiling.ceiling_minor_units,
                ceiling.committed_minor_units,
                period,
            )


class PgProcessedEventStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def is_processed(self, key: str, *, org_id: str) -> bool:
        async with self._db.tenant_session(org_id) as conn:
            found = await conn.fetchval(
                "SELECT 1 FROM decision_processed_events WHERE key = $1", key
            )
        return found is not None

    async def mark_processed(self, key: str, outcome: str, *, org_id: str) -> None:
        async with self._db.tenant_session(org_id) as conn:
            await conn.execute(
                """
                INSERT INTO decision_processed_events (org_id, key, outcome)
                VALUES ($1, $2, $3)
                ON CONFLICT (org_id, key) DO NOTHING
                """,
                org_id,
                key,
                outcome,
            )
