"""Harvest a REAL signal for a scheduled autonomous run.

ONE HARVESTER PER AGENT, DISPATCHED BY AGENT ID (``harvest_for`` at the bottom).
The shape of a signal is a property of the agent's own input schema, so there is
no generic harvester to write: ``fraud_detection_agent`` takes counts over a
window, ``brand_guardian_agent`` takes one piece of content. What IS generic --
and what the dispatch preserves -- is the rule that a scheduled run's input comes
from something already in the database, never from a literal invented here.

Each harvester returns ``None`` rather than raising when it has no signal, and
what ``None`` MEANS is per-agent: the fraud sweep falls back to its descriptor
and still runs, the brand sweep is skipped entirely. Both are argued where they
are implemented; neither is a default.

=============================================================================
fraud_detection_agent -- counted activity over a window
=============================================================================

WHAT CHANGED, AND WHY IT IS ALLOWED TO. ``pilot.py``'s sweep descriptor carries
``features={}`` and says so honestly: with no store to read, an invented number
would be a fabricated input on a governance record. This module supplies the
store that claim was missing -- ``audit_log``, already written by the live
request path on every governed action -- so the scheduled run now submits
COUNTED facts instead of a bare review request. The descriptor remains the
fallback for the case below.

SHAPE. One signal per sweep, whose ``entity_id`` is the org: the scheduled trigger
fires once per org per cadence, so one run reports on one window. Per-agent
signals would be a different trigger shape (one run each), not a bigger payload,
and that is a separate decision.

THE FEATURES ARE COUNTS, PLUS ONE RATIO. ``denial_rate`` is included because it
is the one derived number that does not need a threshold to be meaningful --
20 denials out of 20 actions and 20 out of 20000 are different situations, and
the agent cannot see the second without the divisor. Every other feature is a
raw count. NO thresholding, scoring or classification happens here: deciding
whether a window is fraudulent is the agent's job, under its contract and its
governance, and a rule invented in this module would be an ungoverned verdict
wearing an input's clothes.

WINDOW = CADENCE. The sweep covers exactly the interval since the previous
firing (``PILOT_CRON`` is hourly), so consecutive runs partition the timeline
once and a denial is reported by exactly one sweep.

FAIL-OPEN TO THE DESCRIPTOR, DELIBERATELY. If the signal source is absent or the
read fails, the caller falls back to ``pilot_input()`` rather than skipping the
run. The agent is ``FAIL_CLOSED`` about its VERDICT, which is the thing that
matters; a sweep that cannot read its window should still run and say so, not
vanish silently from the journal. The distinction is recorded in ``signal_kind``
-- ``audit_window`` when counted, ``scheduled_review`` when not -- so no reader
of a governance record has to guess which one it is looking at.

=============================================================================
brand_guardian_agent -- one authored deliverable, reviewed
=============================================================================

THE SOURCE NEEDS NO ARGUING. ``BrandCheckIn`` asks for a piece of content, and
``deliverables.content_markdown`` (migration 0006) IS authored content, written
by the live request path on every agent run. Unlike the fraud sweep, nothing has
to be derived, counted or shaped to fit: the agent's whole job is to rule on copy
another agent wrote, and that copy is already a row.

``content_kind`` IS KEYED ON THE PRODUCING AGENT, NOT ON ``deliverable_type``,
and that is the one judgement in this file. ``BrandCheckIn.content_kind`` is
documented as ``'hook' | 'copy' | 'caption' | 'script'``
(schemas/agents/brand.py:18) while ``deliverable_type`` is a different, ten-value
CHECK-constrained vocabulary (marketing_copy, ad_creative, social_post, ...). The
two do not map cleanly onto each other -- ``ad_creative`` could be a visual asset
or ad copy depending on who wrote it. The PRODUCER does determine it, because
four authoring agents are named for exactly those four kinds, so the mapping
below is each agent's own name rather than an inference:

    hook_generator_agent -> hook      ad_copy_agent        -> copy
    caption_writer_agent -> caption   script_writer_agent  -> script

Anything else is EXCLUDED rather than labelled. ``cta_optimizer_agent`` and
``copy_director`` also emit copy, and calling their output ``'copy'`` would be a
defensible guess -- but a guess stamped onto a governance record, which is the
thing ``_AGENT_DELIVERABLE_TYPE`` refuses when it writes "other" rather than a
narrower term it cannot justify (execution.py:95-104). Same discipline here: the
allowlist is the four exact matches, and widening it is a decision someone makes
on purpose.

NO SUBJECT MEANS NO RUN. When the window holds nothing unreviewed the harvester
returns ``None`` and the caller SKIPS -- it must not fall back to a descriptor
the way the fraud sweep does. Two reasons, and the first is sufficient: the
fallback descriptor is shaped for ``ActivitySignalIn`` and would fail validation
against ``BrandCheckIn``, turning every quiet cadence into a ``failed`` journal
row. The second is that it would deserve to fail anyway -- "no anomalies this
hour" is a real observation about fraud, but a brand verdict about content that
does not exist is not an observation about anything.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence

from .pilot import CONTENT_REVIEW_AGENT_ID, PILOT_AGENT_ID

if TYPE_CHECKING:
    from skylize.dal.ports import AuditWindowCounts, UncheckedContentRow

log = logging.getLogger(__name__)

#: `signal_kind` for a signal carrying real counted activity. Distinct from the
#: descriptor's `scheduled_review`, so the two are never confused on a record.
SIGNAL_KIND_AUDIT_WINDOW = "audit_window"

#: The sweep window. Matches `PILOT_CRON` (hourly): a window shorter than the
#: cadence would drop activity nobody ever reports on.
SWEEP_WINDOW = timedelta(hours=1)


class ActivitySignalSource(Protocol):
    """The port the trigger depends on, so `app` holds no SQL (import-linter
    contract "Application logic contains no SQL")."""

    async def read_window(
        self, *, org_id: str, since: datetime, until: datetime
    ) -> "AuditWindowCounts": ...


def signal_from_counts(counts: "AuditWindowCounts") -> dict[str, Any]:
    """Build the `ActivitySignalIn` payload from one window's counts.

    Pure, so the mapping from counts to features can be asserted without a
    database -- the features are what lands on a governance record.
    """
    total = float(counts.total)
    denied = float(counts.by_result.get("denied", 0))
    failed = float(counts.by_result.get("failed", 0))
    return {
        "entity_id": counts.org_id,
        "signal_kind": SIGNAL_KIND_AUDIT_WINDOW,
        "features": {
            "window_seconds": (counts.until - counts.since).total_seconds(),
            "total_actions": total,
            "success": float(counts.by_result.get("success", 0)),
            "denied": denied,
            "escalated": float(counts.by_result.get("escalated", 0)),
            "failed": failed,
            # Ratios over an empty window are 0.0, not undefined: "no actions"
            # is a quiet hour, and a NaN on a governance record helps nobody.
            "denial_rate": (denied / total) if total else 0.0,
            "failure_rate": (failed / total) if total else 0.0,
            "distinct_action_types": float(counts.distinct_action_types),
            "distinct_agents": float(counts.distinct_agents),
        },
    }


async def harvest_activity_signal(
    source: ActivitySignalSource | None,
    *,
    org_id: str,
    now: datetime,
    window: timedelta = SWEEP_WINDOW,
) -> dict[str, Any] | None:
    """The org's counted activity over the last `window`, or None to fall back.

    Returns None -- never raises -- when there is no source or the read fails,
    because the caller's contract is "run the sweep anyway and say which input
    it got"; see the module docstring.
    """
    if source is None:
        return None
    try:
        counts = await source.read_window(
            org_id=org_id, since=now - window, until=now
        )
    except Exception:  # noqa: BLE001 - see module docstring: never block the run
        return None
    return signal_from_counts(counts)


# --------------------------------------------------------------------------- #
# brand_guardian_agent -- one authored deliverable, reviewed
# --------------------------------------------------------------------------- #

#: Producing agent -> the `content_kind` its output is. Each entry is the agent's
#: own name matching one of the four documented kinds; see the module docstring
#: for why a near-miss is excluded rather than approximated.
CONTENT_KIND_BY_PRODUCER: Mapping[str, str] = {
    "hook_generator_agent": "hook",
    "ad_copy_agent": "copy",
    "caption_writer_agent": "caption",
    "script_writer_agent": "script",
}

#: How far back a backlog is worth draining. NOT the cadence: one firing reviews
#: one deliverable, so a window equal to the cadence would abandon anything it
#: could not reach in time. A day is the point past which a brand verdict on
#: already-shipped copy stops being a control and starts being archaeology.
CONTENT_LOOKBACK = timedelta(hours=24)


class ContentSignalSource(Protocol):
    """The port the trigger depends on, so `app` holds no SQL (import-linter
    contract "Application logic contains no SQL")."""

    async def read_oldest_unchecked(
        self,
        *,
        org_id: str,
        producer_agent_ids: Sequence[str],
        reviewer_agent_id: str,
        since: datetime,
        until: datetime,
    ) -> "UncheckedContentRow | None": ...


def content_signal_from_row(row: "UncheckedContentRow") -> dict[str, Any]:
    """Build the `BrandCheckIn` payload from one un-reviewed deliverable.

    Pure, so the mapping can be asserted without a database -- these three values
    are what lands on a governance record and what the agent is asked about.

    `brief_id` is the deliverable's OWN id, not a fresh one. That is what makes
    the verdict joinable back to the thing it judged, and it is the same id
    `read_oldest_unchecked` reads back out of the reviewer's metadata to know
    this deliverable has been handled.
    """
    return {
        "brief_id": str(row.deliverable_id),
        "content": row.content_markdown,
        "content_kind": CONTENT_KIND_BY_PRODUCER[row.producing_agent_id],
    }


async def harvest_content_signal(
    source: "ContentSignalSource | None",
    *,
    org_id: str,
    reviewer_agent_id: str,
    now: datetime,
    lookback: timedelta = CONTENT_LOOKBACK,
) -> dict[str, Any] | None:
    """The oldest un-reviewed authored deliverable, or None to SKIP the run.

    Returns None -- never raises -- when there is no source, the read fails, or
    there is simply nothing to review. Unlike the fraud sweep the caller must NOT
    substitute a descriptor for this; see the module docstring.
    """
    if source is None:
        return None
    try:
        row = await source.read_oldest_unchecked(
            org_id=org_id,
            producer_agent_ids=sorted(CONTENT_KIND_BY_PRODUCER),
            reviewer_agent_id=reviewer_agent_id,
            since=now - lookback,
            until=now,
        )
    except Exception:  # noqa: BLE001 - a failed read skips, never crashes a sweep
        log.error(
            "content_signal_read_failed",
            extra={"org_id": org_id, "reviewer_agent_id": reviewer_agent_id},
            exc_info=True,
        )
        return None
    if row is None:
        return None
    if row.backlog > 1:
        # One firing reviews one item, so the remainder waits for the next. The
        # only place that fact is visible: nothing downstream can carry it,
        # because `BrandCheckIn` forbids extra fields and inventing one would put
        # a number on a governance record that the agent was never asked about.
        log.info(
            "content_signal_backlog",
            extra={
                "org_id": org_id,
                "reviewer_agent_id": reviewer_agent_id,
                "backlog": row.backlog,
                "oldest_created_at": row.created_at.isoformat(),
            },
        )
    return content_signal_from_row(row)


# --------------------------------------------------------------------------- #
# The dispatch
# --------------------------------------------------------------------------- #

async def harvest_for(
    agent_id: str,
    source: Any | None,
    *,
    org_id: str,
    now: datetime,
) -> dict[str, Any] | None:
    """This agent's harvested input, or None when it has no signal.

    The ONLY agent-aware seam in the harvest path. A caller (the Temporal
    activity) holds a per-agent map of sources and does not otherwise know which
    shape any of them produces.

    An agent with no harvester registered returns None, which is correct and not
    a gap: it means "this agent has no wired signal source", and what to do about
    that is the caller's decision -- `pilot.py` decides whether a fallback exists.
    """
    if agent_id == PILOT_AGENT_ID:
        return await harvest_activity_signal(source, org_id=org_id, now=now)
    if agent_id == CONTENT_REVIEW_AGENT_ID:
        return await harvest_content_signal(
            source, org_id=org_id, reviewer_agent_id=agent_id, now=now
        )
    return None
