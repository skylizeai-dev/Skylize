"""Content-signal DAL — real authored content read off ``deliverables``.

WHY THIS EXISTS. ``brand_guardian_agent``'s input is
``BrandCheckIn{brief_id, content, content_kind}`` (schemas/agents/brand.py:16-19):
a PIECE OF CONTENT, not a descriptor. The pilot's sweep had to invent its input
until ``audit_log`` was found for it; this agent needs no invention at all,
because the content it exists to review is already in the database.
``deliverables`` (migration 0006) is written by the live request path on EVERY
agent run (``app/agents/execution.py:501`` ``create_deliverable``), and
``content_markdown`` is the authored text itself.

WHAT IT SELECTS, AND WHY THOSE ROWS. The oldest deliverable, produced by one of
the named authoring agents, that this reviewer has not already ruled on. Three
choices, each load-bearing:

  * PRODUCED BY A NAMED AGENT, never "every deliverable". Every agent run writes
    a row, including the reviewer's own and the fraud sweep's, so an unfiltered
    sweep would feed the reviewer its own verdicts and call the result a brand
    check. The caller passes the producer allowlist; see
    ``app/autonomy/signals.py`` for why it is keyed on the PRODUCING AGENT rather
    than on ``deliverable_type``.
  * NOT ALREADY CHECKED, derived from data that already exists. A run's
    ``input_data`` is persisted verbatim into the deliverable's
    ``metadata_json->'input'`` (execution.py:496-497), so a past check of
    deliverable X is exactly a row by the reviewer whose
    ``metadata_json->'input'->>'brief_id'`` is X. No new column, no new table and
    no second source of truth for "has this been reviewed" -- the reviewer's own
    governance record is the record.
  * OLDEST FIRST. One firing checks one deliverable, so a queue that arrives
    faster than the cadence drains would starve its tail forever under
    newest-first. Oldest-first makes the backlog a delay rather than a hole.

TENANCY. The read runs inside ``Database.tenant_session(org_id)``, so migration
0006's ``tenant_isolation`` policy on ``deliverables`` applies and a sweep can
only ever see the org's own content. The runtime connects as the NOBYPASSRLS
``skylize_app`` role, so that is a real boundary and not a convention.

NO WRITES. Same rule as ``activity_signals.py``: a signal source that could write
could manufacture the evidence it reports on. ``PgDeliverableRepository`` is the
writer and is a separate class on purpose -- this one holds SELECT only.

NOTHING TO CHECK IS A RESULT, NOT A FAILURE, and it is ``None`` rather than a
zeroed row. That is the opposite of ``AuditWindowCounts``, deliberately: a quiet
hour is a true observation ABOUT fraud ("no denials"), but there is no such thing
as a brand verdict about content that does not exist. The caller must skip the
run, not invent a subject for it.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Sequence

from skylize.dal.ports import UncheckedContentRow

if TYPE_CHECKING:
    from skylize.dal.connection import Database

__all__ = ["DeliverableContentSignalDAL", "UncheckedContentRow"]


class DeliverableContentSignalDAL:
    """Reads `deliverables`. No writes -- see the module docstring."""

    def __init__(self, db: "Database") -> None:
        self._db = db

    async def read_oldest_unchecked(
        self,
        *,
        org_id: str,
        producer_agent_ids: Sequence[str],
        reviewer_agent_id: str,
        since: datetime,
        until: datetime,
    ) -> UncheckedContentRow | None:
        """The oldest un-reviewed authored deliverable in ``[since, until]``.

        ``since`` is a LOOKBACK FLOOR, not a cadence window. A firing checks one
        item, so a window equal to the cadence would abandon everything it could
        not reach before the next one; the floor instead bounds how far back a
        backlog is worth draining, and content older than it is deliberately let
        go rather than reviewed uselessly late.

        ``until`` IS INCLUSIVE, unlike ``AuditActivitySignalDAL``'s, and the
        difference is deliberate rather than an inconsistency. That one is
        half-open because consecutive sweeps must PARTITION the timeline -- the
        window is the only thing stopping one denial being counted by two sweeps.
        Here the window does no such work: double-review is prevented by the
        ``NOT EXISTS`` join below, which is a fact about the content rather than
        about when it was looked at. So an exclusive bound would buy nothing and
        cost a real edge -- a deliverable created in the same clock tick as the
        sweep's ``now`` would be skipped, and on Windows ``datetime.now()`` has
        ~15.6ms granularity, so "the same tick" is a millisecond-wide hole that a
        run triggered right after a publish falls straight into.

        Returns ``None`` when there is nothing to check. ``backlog`` on a
        returned row counts every candidate in the window, the returned one
        included, so ``backlog == 1`` means the queue is drained by this run.
        """
        if not producer_agent_ids:
            # An empty allowlist would otherwise select nothing while looking
            # like a working sweep. Refusing here keeps "no producers wired" and
            # "no content produced" distinguishable at the call site.
            return None

        producers = list(producer_agent_ids)
        async with self._db.tenant_session(org_id) as conn:
            row = await conn.fetchrow(
                """
                WITH candidate AS (
                    SELECT d.id, d.org_id, d.agent_id, d.deliverable_type,
                           d.title, d.content_markdown, d.created_at
                    FROM deliverables d
                    WHERE d.org_id = $1
                      AND d.agent_id = ANY($2::text[])
                      AND d.created_at >= $3
                      AND d.created_at <= $4
                      AND NOT EXISTS (
                          SELECT 1
                          FROM deliverables c
                          WHERE c.org_id = d.org_id
                            AND c.agent_id = $5
                            AND c.metadata_json -> 'input' ->> 'brief_id'
                                = d.id::text
                      )
                )
                SELECT *, (SELECT count(*) FROM candidate) AS backlog
                FROM candidate
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """,
                org_id,
                producers,
                since,
                until,
                reviewer_agent_id,
            )
        if row is None:
            return None
        return UncheckedContentRow(
            deliverable_id=row["id"],
            org_id=row["org_id"],
            producing_agent_id=row["agent_id"],
            deliverable_type=row["deliverable_type"],
            title=row["title"],
            content_markdown=row["content_markdown"],
            created_at=row["created_at"],
            backlog=int(row["backlog"]),
        )
