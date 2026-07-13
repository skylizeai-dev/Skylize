"""asyncpg implementation of WorkflowRepository."""

from __future__ import annotations

import json
from typing import Any

from .connection import Database
from .ports import WorkflowRunStepRow


def _jsonb(value: dict[str, Any] | None) -> str | None:
    """Encode a dict for a JSONB column, preserving SQL NULL.

    json.dumps(None) is the string "null", which lands in JSONB as a JSON null
    rather than a SQL NULL — so absent values must stay Python None.
    """
    return None if value is None else json.dumps(value)


class PgWorkflowRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record_step(self, row: WorkflowRunStepRow) -> None:
        async with self._db.tenant_session(row.org_id) as conn:
            await conn.execute(
                """
                INSERT INTO workflow_run_steps (
                    step_id, run_id, org_id, step_name, step_order, agent_id,
                    status, input, output, judge_verdict, error_message,
                    retry_count, created_at, completed_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                """,
                row.step_id, row.run_id, row.org_id, row.step_name, row.step_order,
                row.agent_id, row.status,
                json.dumps(row.input),
                _jsonb(row.output),
                _jsonb(row.judge_verdict),
                row.error_message, row.retry_count, row.created_at, row.completed_at,
            )
