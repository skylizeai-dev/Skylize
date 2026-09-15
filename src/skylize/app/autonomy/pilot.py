"""THE PILOT SCOPE GATE — exactly one agent may be triggered autonomously.

Owner decision (2026-09-13), hard exit gate: build and prove the mechanism for
ONE agent. Do not wire all 23. This module is where that limit is expressed, and
it is expressed ONCE: `runner.py`, `principal.py` and `attention.py` are all
agent-agnostic, so the only thing standing between this pilot and a 23-agent
fleet is `PILOT_AGENT_IDS` -- and `assert_pilot_agent`, which makes bypassing it
an exception rather than an omission.

WHY `fraud_detection_agent`
---------------------------
Chosen against the owner's three criteria (contracts/mvp/security.py:10-36):

  read-only / lowest blast radius
      `allowed_tools` is exactly {llm.generate, memory.search}. No money movement,
      no external write, not even the outbound network read `seo_keyword_agent`
      holds (`search.web`). `invocable_tools` is empty, so it takes the
      single-shot path and never the tool loop. `authority_level="worker"` -- the
      lowest rung -- and `failure_mode=FAIL_CLOSED`.

  resolvable chain ending in a real human
      escalation_path = [manager_security_operations, director_cybersecurity,
      chief_security_officer, human_owner].

  a REAL boundary to prove `requires_attention` against
      it declares SECURITY_SEVERITY_HIGH and LOW_CONFIDENCE_IRREVERSIBLE, and its
      output carries `outcome` and `confidence` (schemas/agents/security.py:16-19).
      So "hit a HITL-eligible boundary" is a genuine predicate over real fields,
      not a hardcoded True. Most read-only agents in the set emit prose with
      nothing a boundary could be read off.

THE COMPROMISE, STATED PLAINLY
------------------------------
No agent in the 23 cleanly satisfies "read-only AND naturally scheduled", and
this one is a closest fit, not a clean fit. Its input is
`ActivitySignalIn{entity_id, signal_kind, features}` -- PER-SIGNAL -- while the
scheduled shape fires once per org per cadence, so a sweep has to report on a
WINDOW rather than on one signal. The genuinely natural shape for fraud detection
is event-driven, which is why `triggers.py` implements both and this agent can be
moved onto the event shape with no change to the runner.

UPDATE (2026-09-15): the sweep now carries real counts. This module used to say
there was "no activity-signal store anywhere in src/" to populate `features`
from. That was too strong: `audit_log` (migration 0001) is one, it is written by
the live request path on every governed action, and its `denied`/`failed` rows
are exactly the security-relevant tail. `dal/activity_signals.py` reads a window
of it and `app/autonomy/signals.py` maps that window onto `ActivitySignalIn`.
`PILOT_SWEEP_INPUT` below survives as the FALLBACK for a run whose window could
not be read -- see `signals.py` for why that falls open rather than skipping.
The runner-up, `cfo_agent`, is far more naturally scheduled (a periodic budget
summary, with real spend in `ai_cost_ledger` to summarize) but is
`authority_level="executive"` and takes the multi-turn tool path -- the wrong
blast radius for a first pilot.
"""

from __future__ import annotations

from typing import Any, Mapping

from .attention import has_attention_rules
from .errors import ContractNotAutonomous

#: The pilot agent. ONE entry. A second one belongs to a separate owner decision.
PILOT_AGENT_ID = "fraud_detection_agent"

#: The allowlist the triggers consult. A frozenset of one, rather than a bare
#: string, so widening the pilot later is a data change at a single named symbol.
PILOT_AGENT_IDS: frozenset[str] = frozenset({PILOT_AGENT_ID})

#: Cadence for the scheduled shape: hourly, on the hour. A monitoring sweep that
#: runs less often than the thing it monitors changes is theatre; hourly is the
#: slowest cadence that still reads as monitoring, and the cheapest thing that
#: could possibly demonstrate a recurring trigger. Standard 5-field cron, which
#: is what Temporal's ScheduleSpec accepts.
PILOT_CRON = "0 * * * *"

#: The FALLBACK input, submitted only when a window could not be read. Honest
#: about what it is: a periodic review request, NOT a harvested signal. `features`
#: stays empty because an invented number here would be a fabricated input on a
#: governance record -- and `signal_kind` differs from the harvested signal's
#: `audit_window`, so a reader can always tell the two apart on a record.
PILOT_SWEEP_INPUT: Mapping[str, Any] = {
    "entity_id": "org_periodic_sweep",
    "signal_kind": "scheduled_review",
    "features": {},
}


def pilot_input() -> dict[str, Any]:
    """A fresh mutable copy of the sweep descriptor.

    A copy because `execute()` puts `input_data` into the deliverable's metadata,
    and a shared mutable default that some later caller mutates would rewrite the
    input recorded on every previous run.
    """
    return {
        "entity_id": PILOT_SWEEP_INPUT["entity_id"],
        "signal_kind": PILOT_SWEEP_INPUT["signal_kind"],
        "features": dict(PILOT_SWEEP_INPUT["features"]),
    }


def assert_pilot_agent(agent_id: str) -> None:
    """Refuse any agent outside the pilot allowlist.

    Enforced at every trigger entry point, so an agent cannot become autonomous
    by way of a config typo, a replayed schedule, or a stray event on a stream.
    The hard exit gate is a runtime check, not a comment.
    """
    if agent_id not in PILOT_AGENT_IDS:
        raise ContractNotAutonomous(
            f"agent_id={agent_id!r} is not in the autonomous pilot allowlist "
            f"({sorted(PILOT_AGENT_IDS)}). Enabling another agent is an owner "
            "decision, not a configuration change: it needs attention rules "
            "calibrated for its own declared human_in_loop_triggers "
            "(app/autonomy/attention.py) before its runs can be trusted to "
            "surface a boundary."
        )
    if not has_attention_rules(agent_id):
        # Belt and braces for the day the allowlist is widened first and the
        # rules second. An agent with no calibrated boundary would report every
        # run as clean, which is indistinguishable from never having a problem.
        raise ContractNotAutonomous(
            f"agent_id={agent_id!r} is allowlisted for autonomous runs but has no "
            "attention rules; every run would report as requiring no attention"
        )
