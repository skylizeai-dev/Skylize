"""Activity-signal DAL — real fraud-relevant counts read off ``audit_log``.

WHY THIS EXISTS. ``fraud_detection_agent``'s input is
``ActivitySignalIn{entity_id, signal_kind, features}`` (schemas/agents/security.py:13-16),
and until now the scheduled shape submitted a DESCRIPTOR with ``features={}``:
``pilot.py`` recorded that "there is no activity-signal store anywhere in
``src/`` for a periodic sweep to read", so an invented number would have been a
fabricated input on a governance record. That premise was too strong.
``audit_log`` (migration 0001) IS such a store, and it is already written by the
live request path on every governed action -- ``app/agents/execution.py``
(six sites), ``app/auth/service.py:59,111`` and ``adapters/llm/spend_ceiling.py:298``
-- with ``result`` drawn from ``success|denied|escalated|failed``.

WHAT IT MEASURES, AND WHY THOSE COLUMNS. A window of the org's own governed
actions, counted by outcome. ``denied`` and ``failed`` are the security-relevant
tail: a burst of refused tool invocations or failed approvals is the shape an
abused credential or a misbehaving agent actually leaves in this table. Nothing
here is derived, scored or thresholded -- the DAL returns counts and the agent
does the reasoning, because a number this layer invents is a number no human
reviewed.

TENANCY. The read runs inside ``Database.tenant_session(org_id)``, so the
``tenant_isolation`` RLS policy on ``audit_log`` applies and a sweep can only
ever count the org's own rows -- the same scoping ``OrgAutonomyModeDAL`` uses.
The runtime connects as the NOBYPASSRLS ``skylize_app`` role, so that is a real
boundary rather than a convention.

APPEND-ONLY EVIDENCE. ``audit_log`` refuses DELETE (a BEFORE DELETE trigger;
``tests/integration/test_activity_signal_source_pg.py`` had to TRUNCATE to tear
its fixtures down). That is the property that makes it a defensible source for
this agent: the record a fraud verdict is reasoned from cannot be quietly edited
between the action and the sweep that counts it.

EMPTY IS A RESULT, NOT A FAILURE. A window with no audited action returns zeroed
counts, not None. "Nothing happened this hour" is a true and reportable
observation; turning it into an absent input would make a quiet hour
indistinguishable from a broken sweep.

SECOND READER, SAME DISCIPLINE. ``read_recent_governance_events`` was added for
the console's security-posture screen, which needs the same window this module
already counts plus the actual rows behind the non-success part of it. It lives
here rather than in a module of its own because it reads the same table, in the
same window, under the same ``tenant_session`` -- splitting it out would have
duplicated the tenancy and windowing reasoning above and let the two drift.

WHAT THAT SCREEN DELIBERATELY DOES NOT GET. No posture score, no control
inventory, no compliance status. None of those has a source anywhere in this
schema, and the docstring rule at the top of this module -- a number this layer
invents is a number no human reviewed -- applies with more force, not less, to a
screen a reader will take as an assurance. The counts and the rows are what is
real; their ABSENCE of a score is the honest signal.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from skylize.dal.ports import AuditWindowCounts, GovernanceEventRow

if TYPE_CHECKING:
    from skylize.dal.connection import Database

__all__ = [
    "AUDIT_RESULTS",
    "ATTENTION_RESULTS",
    "AuditActivitySignalDAL",
    "AuditWindowCounts",
    "GovernanceEventRow",
]

#: The `result` values migration 0001's audit_log carries, as written by
#: `AuditService.record` (app/audit/service.py:47). Counted explicitly rather
#: than grouped dynamically so a new result value shows up as a gap to decide
#: about, not as a silently-missing feature.
AUDIT_RESULTS: tuple[str, ...] = ("success", "denied", "escalated", "failed")

#: The subset of `AUDIT_RESULTS` the console's security-posture feed shows.
#: NOT a severity ranking and NOT a risk classification -- it is the set of
#: outcomes where governance did something OTHER than let the action through,
#: which is the only reading of these rows this layer is entitled to. `success`
#: is excluded because a feed of successes is a throughput report, not a
#: governance one; the COUNT of successes is still reported beside it so the
#: denominator is never hidden.
ATTENTION_RESULTS: tuple[str, ...] = ("denied", "escalated", "failed")


class AuditActivitySignalDAL:
    """Reads `audit_log`. No writes: a signal source that could write would be
    able to manufacture the evidence it reports on."""

    def __init__(self, db: "Database") -> None:
        self._db = db

    async def read_window(
        self, *, org_id: str, since: datetime, until: datetime
    ) -> AuditWindowCounts:
        """Count the org's audited actions in ``[since, until)``.

        Half-open on purpose: consecutive hourly sweeps then partition the
        timeline exactly once, so an action on the boundary is counted by one
        sweep rather than by both.
        """
        async with self._db.tenant_session(org_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    count(*)                                        AS total,
                    count(*) FILTER (WHERE result = 'success')      AS n_success,
                    count(*) FILTER (WHERE result = 'denied')       AS n_denied,
                    count(*) FILTER (WHERE result = 'escalated')    AS n_escalated,
                    count(*) FILTER (WHERE result = 'failed')       AS n_failed,
                    count(DISTINCT action_type)                     AS n_action_types,
                    count(DISTINCT source_agent_id)                 AS n_agents
                FROM audit_log
                WHERE org_id = $1 AND occurred_at >= $2 AND occurred_at < $3
                """,
                org_id,
                since,
                until,
            )
        counts = {
            "success": int(row["n_success"]),
            "denied": int(row["n_denied"]),
            "escalated": int(row["n_escalated"]),
            "failed": int(row["n_failed"]),
        }
        return AuditWindowCounts(
            org_id=org_id,
            since=since,
            until=until,
            total=int(row["total"]),
            by_result=counts,
            distinct_action_types=int(row["n_action_types"]),
            distinct_agents=int(row["n_agents"]),
        )

    async def read_recent_governance_events(
        self,
        *,
        org_id: str,
        since: datetime,
        until: datetime,
        limit: int = 20,
        results: tuple[str, ...] = ATTENTION_RESULTS,
    ) -> list[GovernanceEventRow]:
        """The org's most recent NON-SUCCESS audited actions in ``[since, until)``.

        THE SAME WINDOW AS ``read_window``, on purpose. The console shows these
        rows directly beneath that window's counts, so a row a reader can see
        but not find in the counts -- or a count with no row behind it -- would
        be a contradiction on the one screen that must not produce one. Callers
        pass both the same bounds.

        NO SCORE, NO SEVERITY, NO RANKING. Ordered by ``occurred_at DESC``, the
        order the actions happened in, which is the only ordering this table
        supports without inventing a weighting no human has reviewed. A caller
        that wants "the worst thing that happened" must decide for itself what
        worst means; this layer does not know and does not guess.

        ``limit`` bounds the page, not the truth: ``read_window`` above counts
        the FULL window, so a truncated feed sits next to an untruncated count
        and the caller can always tell that it is looking at a sample.
        """
        async with self._db.tenant_session(org_id) as conn:
            rows = await conn.fetch(
                """
                SELECT
                    event_id, correlation_id, action_type, result, occurred_at,
                    source_agent_id, authority_level, governance_token_id,
                    result_reason
                FROM audit_log
                WHERE org_id = $1
                  AND occurred_at >= $2
                  AND occurred_at < $3
                  AND result = ANY($4::text[])
                ORDER BY occurred_at DESC
                LIMIT $5
                """,
                org_id,
                since,
                until,
                list(results),
                limit,
            )
        return [
            GovernanceEventRow(
                event_id=r["event_id"],
                correlation_id=r["correlation_id"],
                action_type=r["action_type"],
                result=r["result"],
                occurred_at=r["occurred_at"],
                source_agent_id=r["source_agent_id"],
                authority_level=r["authority_level"],
                governance_token_id=r["governance_token_id"],
                result_reason=r["result_reason"],
            )
            for r in rows
        ]
