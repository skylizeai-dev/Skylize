"""asyncpg implementation of skylize.app.principal.journal.JournalRepository.

Mirrors PgDeliverableRepository's shape exactly (dal/deliverables.py): a
`Database` dependency, one `self._db.tenant_session(org_id)` block per method,
no locally held connection or transaction state. Backs `work_journal` /
`journal_cursor` (migration 0019).

`work_journal` is append-only at the schema level (the `work_journal_append_only`
trigger, migration 0019) and the `skylize_app` role is granted only SELECT,
INSERT on it — this repository never attempts UPDATE/DELETE against it.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..app.principal.models import ActorKind, JournalCursor, JournalEntry
from .connection import Database


def _entry(rec: Any) -> JournalEntry:
    return JournalEntry(
        seq=rec["seq"],
        org_id=rec["org_id"],
        principal_id=rec["principal_id"],
        actor_kind=ActorKind(rec["actor_kind"]),
        actor_id=rec["actor_id"],
        correlation_id=rec["correlation_id"],
        governance_token_id=rec["governance_token_id"],
        kind=rec["kind"],
        headline=rec["headline"],
        detail=rec["detail"] or {},
        cost_minor=rec["cost_minor"],
        requires_attention=rec["requires_attention"],
        occurred_at=rec["occurred_at"],
    )


def _cursor(rec: Any) -> JournalCursor:
    return JournalCursor(
        org_id=rec["org_id"],
        principal_id=rec["principal_id"],
        last_seen_seq=rec["last_seen_seq"],
        last_seen_at=rec["last_seen_at"],
    )


class PostgresJournalRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def append(self, entry: JournalEntry) -> int:
        async with self._db.tenant_session(entry.org_id) as conn:
            seq: int = await conn.fetchval(
                """
                INSERT INTO work_journal (
                    org_id, principal_id, actor_kind, actor_id, correlation_id,
                    governance_token_id, kind, headline, detail, cost_minor,
                    requires_attention, occurred_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                RETURNING seq
                """,
                entry.org_id,
                entry.principal_id,
                entry.actor_kind.value,
                entry.actor_id,
                entry.correlation_id,
                entry.governance_token_id,
                entry.kind,
                entry.headline,
                json.dumps(entry.detail),
                entry.cost_minor,
                entry.requires_attention,
                entry.occurred_at,
            )
            return seq

    async def since(
        self,
        *,
        org_id: str,
        principal_id: str,
        after_seq: int,
        limit: int = 200,
    ) -> list[JournalEntry]:
        async with self._db.tenant_session(org_id) as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM work_journal
                 WHERE org_id = $1 AND principal_id = $2 AND seq > $3
                 ORDER BY seq ASC
                 LIMIT $4
                """,
                org_id,
                principal_id,
                after_seq,
                limit,
            )
            return [_entry(r) for r in rows]

    async def get_cursor(
        self, *, org_id: str, principal_id: str
    ) -> JournalCursor | None:
        async with self._db.tenant_session(org_id) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM journal_cursor WHERE org_id = $1 AND principal_id = $2",
                org_id,
                principal_id,
            )
            return None if row is None else _cursor(row)

    async def advance_cursor(
        self, *, org_id: str, principal_id: str, to_seq: int, at: datetime
    ) -> None:
        async with self._db.tenant_session(org_id) as conn:
            # Monotonic: a race between two POST /me/brief/seen calls can never
            # move the cursor backward.
            await conn.execute(
                """
                INSERT INTO journal_cursor (org_id, principal_id, last_seen_seq, last_seen_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (org_id, principal_id) DO UPDATE
                    SET last_seen_seq = EXCLUDED.last_seen_seq,
                        last_seen_at = EXCLUDED.last_seen_at
                 WHERE journal_cursor.last_seen_seq < EXCLUDED.last_seen_seq
                """,
                org_id,
                principal_id,
                to_seq,
                at,
            )

    async def head_seq(self, *, org_id: str, principal_id: str) -> int:
        async with self._db.tenant_session(org_id) as conn:
            value = await conn.fetchval(
                "SELECT COALESCE(MAX(seq), 0) FROM work_journal "
                "WHERE org_id = $1 AND principal_id = $2",
                org_id,
                principal_id,
            )
            return int(value)
