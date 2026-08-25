"""asyncpg implementation of DeliverableRepository."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from .connection import Database
from .ports import DeliverableRow


def _row(rec: Any) -> DeliverableRow:
    return DeliverableRow(
        id=rec["id"],
        org_id=rec["org_id"],
        agent_id=rec["agent_id"],
        governance_token_id=rec["governance_token_id"],
        deliverable_type=rec["deliverable_type"],
        title=rec["title"],
        content_markdown=rec["content_markdown"],
        summary=rec["summary"],
        status=rec["status"],
        version=rec["version"],
        parent_id=rec["parent_id"],
        metadata_json=rec["metadata_json"] or {},
        created_at=rec["created_at"],
        updated_at=rec["updated_at"],
        approved_at=rec["approved_at"],
        approved_by=rec["approved_by"],
    )


class PgDeliverableRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, row: DeliverableRow) -> None:
        async with self._db.tenant_session(row.org_id) as conn:
            await conn.execute(
                """
                INSERT INTO deliverables (
                    id, org_id, agent_id, governance_token_id, deliverable_type,
                    title, content_markdown, summary, status, version, parent_id,
                    metadata_json, created_at, updated_at, approved_at, approved_by
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                """,
                row.id, row.org_id, row.agent_id, row.governance_token_id,
                row.deliverable_type, row.title, row.content_markdown, row.summary,
                row.status, row.version, row.parent_id,
                json.dumps(row.metadata_json),
                row.created_at, row.updated_at, row.approved_at, row.approved_by,
            )

    async def get_by_id(self, id: UUID, org_id: str) -> DeliverableRow | None:
        async with self._db.tenant_session(org_id) as conn:
            rec = await conn.fetchrow(
                "SELECT * FROM deliverables WHERE id=$1 AND org_id=$2",
                id, org_id,
            )
            return None if rec is None else _row(rec)

    async def list_by_org(
        self,
        org_id: str,
        *,
        status: str | None = None,
        deliverable_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DeliverableRow], int]:
        conditions: list[str] = ["org_id=$1"]
        params: list[Any] = [org_id]
        idx = 2

        if status is not None:
            conditions.append(f"status=${idx}")
            params.append(status)
            idx += 1
        if deliverable_type is not None:
            conditions.append(f"deliverable_type=${idx}")
            params.append(deliverable_type)
            idx += 1

        where = " AND ".join(conditions)

        async with self._db.tenant_session(org_id) as conn:
            total: int = await conn.fetchval(
                f"SELECT COUNT(*) FROM deliverables WHERE {where}", *params
            )
            params_page = list(params) + [limit, offset]
            rows = await conn.fetch(
                f"SELECT * FROM deliverables WHERE {where} "
                f"ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx + 1}",
                *params_page,
            )
            return [_row(r) for r in rows], total

    async def update_status(self, id: UUID, org_id: str, status: str) -> bool:
        async with self._db.tenant_session(org_id) as conn:
            tag = await conn.execute(
                "UPDATE deliverables SET status=$3, updated_at=now() "
                "WHERE id=$1 AND org_id=$2",
                id, org_id, status,
            )
            return bool(str(tag).split()[-1] != "0")

    async def update_approved(
        self, id: UUID, org_id: str, approved_by: str, approved_at: Any
    ) -> bool:
        async with self._db.tenant_session(org_id) as conn:
            tag = await conn.execute(
                "UPDATE deliverables SET status='approved', approved_by=$3, "
                "approved_at=$4, updated_at=now() WHERE id=$1 AND org_id=$2",
                id, org_id, approved_by, approved_at,
            )
            return bool(str(tag).split()[-1] != "0")

    async def list_versions(self, org_id: str, deliverable_id: UUID) -> list[DeliverableRow]:
        async with self._db.tenant_session(org_id) as conn:
            rows = await conn.fetch(
                """
                WITH RECURSIVE
                  ancestors AS (
                    SELECT * FROM deliverables WHERE id=$1 AND org_id=$2
                    UNION ALL
                    SELECT d.* FROM deliverables d
                    INNER JOIN ancestors a ON d.id = a.parent_id AND d.org_id=$2
                  ),
                  root AS (
                    SELECT * FROM ancestors WHERE parent_id IS NULL
                  ),
                  chain AS (
                    SELECT * FROM root
                    UNION ALL
                    SELECT d.* FROM deliverables d
                    INNER JOIN chain c ON d.parent_id = c.id AND d.org_id=$2
                  )
                SELECT * FROM chain ORDER BY version
                """,
                deliverable_id, org_id,
            )
            return [_row(r) for r in rows]
