"""THE SCOPE GATE — only named, calibrated agents may be triggered autonomously.

Owner decision (2026-09-13), hard exit gate: build and prove the mechanism for
ONE agent. Do not wire all 23. This module is where that limit is expressed, and
it is expressed ONCE: `runner.py`, `principal.py` and `attention.py` are all
agent-agnostic, so the only thing standing between this list and a 23-agent
fleet is `PILOT_AGENT_IDS` -- and `assert_pilot_agent`, which makes bypassing it
an exception rather than an omission.

WIDENED TO TWO (2026-09-15, owner decision pending sign-off). The pilot proved
the mechanism end to end and then proved it on a real signal source. The second
entry, `brand_guardian_agent`, is the generalisation test: a DIFFERENT input
shape (one piece of content, not counts over a window), a DIFFERENT signal source
(`deliverables`, not `audit_log`) and a DIFFERENT calibration -- reusing the
runner, the principal resolver and the journal unchanged. Nothing below is a
second copy of the pilot's machinery; the only per-agent facts are the three
tables in this module and the rules in `attention.py`.

WHY `brand_guardian_agent`, against the same three criteria
-----------------------------------------------------------
  read-only / lowest blast radius
      `allowed_tools` is exactly {llm.generate, memory.search} -- byte-identical
      to the pilot's, and the minimum any agent in the set holds. No money
      movement, no external write, no outbound network read. `invocable_tools` is
      empty, so it takes the single-shot path and never the tool loop.
      `memory_write_access` is `[]`, so it cannot even write what it learns.
      `authority_level="worker"` -- the lowest rung -- and
      `failure_mode=FAIL_CLOSED`, the same posture as the pilot.

  resolvable chain ending in a real human
      escalation_path = [vp_creative, cmo, ceo, human_owner]. (This criterion
      does not discriminate: all 23 live contracts terminate at `human_owner`.
      It is a floor every candidate clears, not a reason to prefer this one.)

  a REAL boundary to prove `requires_attention` against
      it declares BRAND_LEGAL_SENSITIVE, and `BrandVerdictOut` carries `outcome`,
      `violations` AND `confidence` (schemas/agents/brand.py:22-26) -- a strict
      superset of the pilot's verdict shape. Across all 14 worker-level
      contracts, this is the ONLY one besides the pilot that has both a declared
      trigger and a verdict-shaped output; every other worker either declares no
      trigger at all (ad_copy, caption_writer, cta_optimizer, script_writer,
      tone_of_voice, lead_qualifier, agency_requirements_analyst) or emits prose
      with nothing a predicate could read (hook_generator's `hooks`,
      agency_deliverable_drafter's `content`). That is a survey result, not a
      preference.

AND IT HAS A REAL SIGNAL SOURCE THAT IS NOT `audit_log`. The point of the second
agent is partly to show the mechanism does not quietly require the pilot's
source. It does not: this agent reads `deliverables`, which the live request path
writes on EVERY agent run, and whose `content_markdown` is literally the copy a
brand reviewer exists to rule on. See `signals.py`.

THE COMPROMISES, STATED PLAINLY
-------------------------------
Two, and neither is hidden.

  ONE ITEM PER FIRING. `BrandCheckIn` is per-content while a cadence fires once
  per org, so a firing reviews exactly one deliverable and the rest wait. Cadence
  is therefore THROUGHPUT here, not freshness -- which is why `CONTENT_CRON` is
  four times the pilot's rate and why the harvester logs a backlog it cannot put
  on the record. An org producing faster than the cadence drains will fall
  behind, visibly in that log and nowhere else.

  THE NATURAL SHAPE IS EVENT-DRIVEN. "Content was produced -> review it" is
  reactive, and `triggers.py` already implements that shape. It is NOT used here
  because nothing publishes a deliverable-created event today: `CreativeReviewRequested`
  exists as a schema (schemas/events/creative.py:44) with no publisher anywhere
  in `src/`, and adding one means editing the live request path -- a wider blast
  radius than this agent's own, and a separate decision. The scheduled shape adds
  nothing to that path, and moving this agent onto the event shape later needs no
  change to the runner.

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

#: The first agent proven autonomous: a periodic fraud sweep over counted
#: activity. See the module docstring for the criteria it was chosen against.
PILOT_AGENT_ID = "fraud_detection_agent"

#: The second: a periodic brand/legal review of authored content.
CONTENT_REVIEW_AGENT_ID = "brand_guardian_agent"

#: The allowlist the triggers consult. Adding a THIRD entry here is not enough to
#: enable one -- `assert_pilot_agent` also requires calibrated attention rules,
#: so an agent added here and nowhere else is refused at the door rather than
#: silently reporting every run as clean. That is tested, not merely intended
#: (tests/unit/test_autonomous_runs.py).
PILOT_AGENT_IDS: frozenset[str] = frozenset({PILOT_AGENT_ID, CONTENT_REVIEW_AGENT_ID})

#: Cadence for the fraud sweep: hourly, on the hour. A monitoring sweep that
#: runs less often than the thing it monitors changes is theatre; hourly is the
#: slowest cadence that still reads as monitoring, and the cheapest thing that
#: could possibly demonstrate a recurring trigger. Standard 5-field cron, which
#: is what Temporal's ScheduleSpec accepts.
PILOT_CRON = "0 * * * *"

#: Cadence for the content review: every 15 minutes. FOUR TIMES the fraud sweep's
#: rate, and the difference is not taste. The fraud sweep reports on a WINDOW, so
#: one firing covers everything that happened in it and the cadence only sets how
#: stale the report may be. This one reviews ONE deliverable per firing, so the
#: cadence is the throughput ceiling: at hourly, an org producing more than one
#: piece of content an hour would fall permanently behind and the queue would
#: grow without bound. 15 minutes buys 4 reviews an hour, which is the cheapest
#: rate that keeps up with a plausible small-team content pace. It does not make
#: the design unbounded -- see the backlog compromise in the module docstring.
CONTENT_CRON = "*/15 * * * *"

#: agent_id -> its cadence. Read by the schedule installer so the cron an
#: operator gets by default is the one argued for THAT agent, rather than
#: whichever happened to be the module-level default.
CRON_BY_AGENT: Mapping[str, str] = {
    PILOT_AGENT_ID: PILOT_CRON,
    CONTENT_REVIEW_AGENT_ID: CONTENT_CRON,
}


def cron_for(agent_id: str) -> str:
    """The declared cadence for `agent_id`. Refuses an agent with none.

    Not a `.get(..., PILOT_CRON)`: inheriting another agent's cadence by default
    is how a per-firing-throughput agent silently ends up on a window agent's
    hourly rate.
    """
    assert_pilot_agent(agent_id)
    try:
        return CRON_BY_AGENT[agent_id]
    except KeyError:  # pragma: no cover - assert_pilot_agent covers the set
        raise ContractNotAutonomous(
            f"agent_id={agent_id!r} is allowlisted but declares no cadence"
        ) from None

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


#: agent_id -> its fallback input, or ABSENT when it has none.
#:
#: DELIBERATELY PARTIAL, and the absence is the point. `fraud_detection_agent`
#: has a fallback because "I could not read my window" is still a reportable
#: observation about a quiet hour. `brand_guardian_agent` has NONE because there
#: is no such thing as a brand verdict on content that does not exist -- and,
#: concretely, `PILOT_SWEEP_INPUT` is shaped for `ActivitySignalIn` and would
#: fail validation against `BrandCheckIn`, so inheriting it would turn every
#: quiet cadence into a `failed` journal row in a human's brief.
_FALLBACK_INPUT: Mapping[str, Mapping[str, Any]] = {
    PILOT_AGENT_ID: PILOT_SWEEP_INPUT,
}


def fallback_input_for(agent_id: str) -> dict[str, Any] | None:
    """A fresh copy of `agent_id`'s fallback input, or None if it has none.

    None means "this agent does not run without a harvested signal" -- the
    caller must skip the firing, not substitute something. Returning a copy for
    the same reason `pilot_input` does.
    """
    declared = _FALLBACK_INPUT.get(agent_id)
    if declared is None:
        return None
    return {
        key: dict(value) if isinstance(value, dict) else value
        for key, value in declared.items()
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
