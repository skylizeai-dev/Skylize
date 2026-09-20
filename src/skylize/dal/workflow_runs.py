"""Workflow run history DAL — run identity on the LIVE orchestrator path.

Read/write layer for ``workflow_runs`` (migration 0033): one row per
``Orchestrator.invoke``, opened ``running`` at the start of an invocation and
closed exactly once when the invocation returns.

A SEPARATE MODULE from ``dal/workflows.py``, deliberately. That module is
``PgWorkflowRepository``, which writes ``workflow_run_steps`` for the Temporal
engine — a different table, a different id space (0010's run_id is a Temporal
workflow run id), and a code path that is PAUSED: nothing in bootstrap.py
constructs it. Folding a live-path lifecycle writer into the paused engine's
repository would blur exactly the boundary migration 0010's docstring drew, and
would make it look as though the step trail and the run row are two halves of
one record. They are not, and no join exists between them.

Both queries run inside ``Database.tenant_session(org_id)`` so the RLS
``tenant_isolation`` policy applies, exactly as ``OrgAutonomyModeDAL`` is
scoped: one org can never read or write another org's run history.

BEST-EFFORT BY CONSTRUCTION is NOT this module's job. These methods raise on
failure like any other DAL. The resilience requirement — that losing a run row
must never lose a run — is enforced at the single call site in
``Orchestrator.invoke``, which swallows and audits. Putting the swallow here
would make every future caller silently unreliable, including ones that need a
write to have happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from skylize.dal.connection import Database

#: The four states a row may hold, mirroring the CHECK constraint in migration
#: 0033. ``running`` is the open interval; the other three are exactly the
#: values ``WorkflowResult.status`` produces (app/orchestrator/orchestrator.py).
#: Validated before the write so a bad value fails legibly rather than as a raw
#: constraint violation.
VALID_RUN_STATUSES: frozenset[str] = frozenset(
    {"running", "completed", "denied", "failed"}
)

#: The statuses that CLOSE a run. ``running`` is not among them: finishing a run
#: as ``running`` would leave ``finished_at`` set on an open row.
TERMINAL_RUN_STATUSES: frozenset[str] = frozenset({"completed", "denied", "failed"})


@dataclass(frozen=True, slots=True)
class WorkflowRunRow:
    """One run as the console reads it.

    There is no stage or progress field. The live graph emits no per-node
    persistence, so a run has a start, an end, a status and — when it stopped
    early — the stage it stopped AT. ``failure_stage`` is where it stopped, not
    how far it got.
    """

    run_id: UUID
    org_id: str
    workflow_name: str
    agent_id: str
    status: str
    correlation_id: UUID
    started_at: datetime
    finished_at: datetime | None
    failure_stage: str | None
    reason: str | None


class WorkflowRunsDAL:
    def __init__(self, db: "Database") -> None:
        self._db = db

    async def start_run(
        self,
        *,
        run_id: UUID,
        org_id: str,
        workflow_name: str,
        agent_id: str,
        correlation_id: UUID,
    ) -> None:
        """Open a run row in ``running``.

        Idempotent on the primary key: a retried invocation carrying the same
        correlation_id does not duplicate the run and does not resurrect a row
        that has already finished. ``DO NOTHING`` rather than ``DO UPDATE``
        precisely so a late duplicate start cannot reopen a closed run.
        """
        async with self._db.tenant_session(org_id) as conn:
            await conn.execute(
                """
                INSERT INTO workflow_runs (
                    run_id, org_id, workflow_name, agent_id, status, correlation_id
                ) VALUES ($1, $2, $3, $4, 'running', $5)
                ON CONFLICT (run_id) DO NOTHING
                """,
                run_id,
                org_id,
                workflow_name,
                agent_id,
                correlation_id,
            )

    async def finish_run(
        self,
        *,
        run_id: UUID,
        org_id: str,
        status: str,
        failure_stage: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Close a run, stamping ``finished_at``.

        The ``status = 'running'`` predicate makes this a one-shot transition:
        an already-closed run keeps its first terminal status and its first
        finished_at. A run that never opened (its start write was swallowed)
        matches nothing and stays absent — a missing run row is a gap in
        history, which is honest, whereas an UPSERT here would invent a run
        with no start instant.
        """
        if status not in TERMINAL_RUN_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(TERMINAL_RUN_STATUSES)}; got {status!r}"
            )
        async with self._db.tenant_session(org_id) as conn:
            await conn.execute(
                """
                UPDATE workflow_runs
                SET status = $3, finished_at = now(),
                    failure_stage = $4, reason = $5
                WHERE run_id = $1 AND org_id = $2 AND status = 'running'
                """,
                run_id,
                org_id,
                status,
                failure_stage,
                reason,
            )

    async def list_runs(
        self,
        org_id: str,
        *,
        limit: int = 50,
        before: datetime | None = None,
    ) -> list[WorkflowRunRow]:
        """One org's run history, newest first.

        Keyset-paginated on ``started_at``, the shape the audit feed uses
        (``AuditService.recent`` / ``edge/routes/audit.py``): pass the last
        row's ``started_at`` back as ``before`` for the next, older page. The
        ``(org_id, started_at DESC)`` index from migration 0033 serves this
        predicate directly.
        """
        async with self._db.tenant_session(org_id) as conn:
            if before is None:
                rows = await conn.fetch(
                    """
                    SELECT run_id, org_id, workflow_name, agent_id, status,
                           correlation_id, started_at, finished_at,
                           failure_stage, reason
                    FROM workflow_runs
                    WHERE org_id = $1
                    ORDER BY started_at DESC
                    LIMIT $2
                    """,
                    org_id,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT run_id, org_id, workflow_name, agent_id, status,
                           correlation_id, started_at, finished_at,
                           failure_stage, reason
                    FROM workflow_runs
                    WHERE org_id = $1 AND started_at < $2
                    ORDER BY started_at DESC
                    LIMIT $3
                    """,
                    org_id,
                    before,
                    limit,
                )
        return [
            WorkflowRunRow(
                run_id=r["run_id"],
                org_id=r["org_id"],
                workflow_name=r["workflow_name"],
                agent_id=r["agent_id"],
                status=r["status"],
                correlation_id=r["correlation_id"],
                started_at=r["started_at"],
                finished_at=r["finished_at"],
                failure_stage=r["failure_stage"],
                reason=r["reason"],
            )
            for r in rows
        ]
