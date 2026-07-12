"""Postgres-backed MemoryAdapter.

Lives in skylize.dal (asyncpg allowed here per import-linter).
Satisfies the MemoryAdapter Protocol defined in skylize.memory.ports.

Behaviour:
  - retrieve: query agent_memory_entries filtered by (org_id, department, session_id);
              exclude superseded entries and expired entries.
  - store:    skip write + log if importance_score < 0.40; otherwise INSERT with
              ON CONFLICT DO NOTHING (idempotent on content_hash + org_id).
  - is_stateless: returns False (contract knowledge lives in AgentRunner, not here).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from ..schemas.memory import MemoryEntry, MemoryScope
from .connection import Database

log = structlog.get_logger(__name__)

_IMPORTANCE_THRESHOLD = 0.40

_SELECT_COLS = (
    "entry_id, org_id, agent_id, scope, department, session_id, "
    "tier, content_text, content_hash, metadata, embedding, "
    "superseded_by, created_at, created_by_agent"
)


class PgMemoryAdapter:
    """Primary MemoryAdapter backed by agent_memory_entries via raw asyncpg."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # MemoryAdapter Protocol
    # ------------------------------------------------------------------

    async def retrieve(self, scope: MemoryScope) -> list[MemoryEntry]:
        clauses = [
            "org_id=$1",
            "superseded_by IS NULL",
            "(expires_at IS NULL OR expires_at > $2)",
        ]
        params: list[Any] = [scope.org_id, datetime.utcnow()]

        if scope.department is not None:
            params.append(scope.department)
            clauses.append(f"department=${len(params)}")
        if scope.session_id is not None:
            params.append(scope.session_id)
            clauses.append(f"session_id=${len(params)}")
        if scope.agent_id is not None:
            params.append(scope.agent_id)
            clauses.append(f"agent_id=${len(params)}")

        sql = (
            f"SELECT {_SELECT_COLS} FROM agent_memory_entries "
            "WHERE " + " AND ".join(clauses) +
            " ORDER BY created_at DESC"
        )
        async with self._db.tenant_session(scope.org_id) as conn:
            rows = await conn.fetch(sql, *params)
        return [_row_to_entry(r) for r in rows]

    async def store(self, scope: MemoryScope, entry: MemoryEntry) -> None:
        importance = entry.metadata.get("importance_score", 0.5)
        if isinstance(importance, (int, float)) and float(importance) < _IMPORTANCE_THRESHOLD:
            log.info(
                "memory.pg_adapter.store.skipped_low_importance",
                org_id=scope.org_id,
                agent_id=entry.agent_id,
                importance_score=importance,
            )
            return

        embedding_str = (
            "[" + ",".join(str(v) for v in entry.embedding) + "]"
            if entry.embedding is not None
            else None
        )
        import json
        async with self._db.tenant_session(scope.org_id) as conn:
            await conn.execute(
                """
                INSERT INTO agent_memory_entries (
                    entry_id, org_id, agent_id, scope, department, session_id,
                    tier, content_text, content_hash, metadata, embedding,
                    superseded_by, created_at, created_by_agent,
                    importance_score
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,
                    $11::vector,$12,$13,$14,$15
                )
                ON CONFLICT (content_hash, org_id) DO NOTHING
                """,
                entry.entry_id,
                entry.org_id,
                entry.agent_id,
                entry.scope,
                entry.department,
                entry.session_id,
                entry.tier,
                entry.content_text,
                entry.content_hash,
                json.dumps(entry.metadata),
                embedding_str,
                entry.superseded_by,
                entry.created_at,
                entry.created_by_agent,
                float(importance),  # type: ignore[arg-type]
            )

    async def is_stateless(self, agent_id: str) -> bool:
        return False


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _row_to_entry(r: Any) -> MemoryEntry:
    raw_embedding = r["embedding"]
    embedding: list[float] | None = None
    if raw_embedding is not None:
        if isinstance(raw_embedding, (list, tuple)):
            embedding = [float(v) for v in raw_embedding]
        elif hasattr(raw_embedding, "tolist"):
            embedding = raw_embedding.tolist()

    import json as _json
    raw_meta = r["metadata"]
    metadata: dict[str, Any] = {}
    if raw_meta is not None:
        if isinstance(raw_meta, str):
            metadata = _json.loads(raw_meta)
        elif isinstance(raw_meta, dict):
            metadata = raw_meta

    return MemoryEntry(
        entry_id=r["entry_id"],
        org_id=r["org_id"],
        agent_id=r["agent_id"],
        scope=r["scope"],
        department=r["department"],
        session_id=r["session_id"],
        tier=r["tier"],
        content_text=r["content_text"],
        content_hash=r["content_hash"],
        metadata=metadata,
        embedding=embedding,
        superseded_by=r["superseded_by"],
        created_at=r["created_at"],
        created_by_agent=r["created_by_agent"],
    )
