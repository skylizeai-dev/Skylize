"""Harvest a REAL activity signal for the scheduled pilot run.

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
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from skylize.dal.ports import AuditWindowCounts

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
