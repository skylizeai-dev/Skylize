from __future__ import annotations

from .models import DecisionOutcome, RiskBand

# THE department vocabulary — single source of truth (ADR-0005, Alternative A,
# accepted 2026-07-19). Department ownership is declared here, sourced from the
# agent contracts that are the real authority on which team owns which work. It
# is NOT inferred from the event type's `{category}.` prefix, because a wire
# type's category namespace and an event's department channel answer different
# questions ("what kind of event is this?" vs "which team's channel does it ride
# on?") and are not required to agree. They notably disagree for the two
# spend-bearing events this engine exists to govern:
#
#   sales.campaign_proposed / sales.budget_reallocation_proposed
#       `sales.` category, but produced by `director_growth`, whose contract is
#       department="growth" (contracts/mvp/growth.py:17). department="sales" is
#       owned by the SDR agents (contracts/mvp/sdr.py:17,39) — a different team
#       that never proposes campaigns.
#   creative.review_requested
#       category and department are both `creative` (contracts/mvp/creative.py),
#       aligned by coincidence rather than by rule.
#
# Every downstream vocabulary below is derived from this one table, so the
# AUTHORITY allow-list and the consumer's subscription set cannot drift apart.
# Adding a department here is an explicit, reviewable governance decision: it
# declares that the engine serves that department and which event types that
# department may raise.
ALLOWED_EVENT_TYPES_BY_DEPARTMENT: dict[str, frozenset[str]] = {
    "creative": frozenset({"creative.review_requested"}),
    "growth": frozenset(
        {
            "sales.campaign_proposed",
            "sales.budget_reallocation_proposed",
        }
    ),
    # `governance` is the ONE department the engine listens to without ever
    # accepting a proposal from it: `governance.human_approval_received` is a
    # human's verdict on a decision this engine already deferred, not a new
    # request to decide. It is declared here — rather than in a second list —
    # so the consumer's subscription set stays derived from this one table
    # (SUBSCRIBED_DEPARTMENTS below), and projected OUT of the AUTHORITY
    # allow-list for the reason spelled out at RESUME_EVENT_TYPES.
    "governance": frozenset({"governance.human_approval_received"}),
}

# Event types that RESUME an already-deferred decision rather than proposing a
# new one. They ride the consumer's addressing filter (so the resume event is
# not silently ignored on `evt:{tenant}:governance`) but must never reach the
# six-stage pipeline: re-evaluating a decision a human has already ruled on
# would let policy silently overturn the human. `consumer._handle_event`
# branches these to the resume handler before `pipeline_fn` is ever called;
# excluding them from AUTHORITY below is the second, independent backstop, so a
# regression in that branch produces a REJECTED decision rather than a policy
# verdict that quietly overrides a person.
RESUME_EVENT_TYPES: frozenset[str] = frozenset({"governance.human_approval_received"})

# The proposal half of the table — what STAGE 1 (AUTHORITY, pipeline.py) may
# accept. Derived, never hand-maintained, so adding a department or event type
# above cannot forget to update it.
PROPOSAL_EVENT_TYPES_BY_DEPARTMENT: dict[str, frozenset[str]] = {
    department: types - RESUME_EVENT_TYPES
    for department, types in ALLOWED_EVENT_TYPES_BY_DEPARTMENT.items()
}

# The departments the engine will accept a PROPOSAL from — STAGE 1 (AUTHORITY).
# A department whose every declared type is a resume type (today: `governance`)
# is deliberately absent: the engine serves it, but never decides for it.
ALLOWED_DEPARTMENTS: frozenset[str] = frozenset(
    department
    for department, types in PROPOSAL_EVENT_TYPES_BY_DEPARTMENT.items()
    if types
)

# The departments the consumer subscribes to — the rebuilt DecisionEngineConsumer
# spawns one EventRouter per (tenant, entry), each on `evt:{tenant}:{department}`
# per the bus's routing key (events/bus.py:27). A SUPERSET of ALLOWED_DEPARTMENTS,
# and the gap is exactly the resume-only departments: the engine must hear a
# channel to be resumed on it, without being authorized to originate decisions
# there. Both sides still project from the one table above, so they cannot drift.
SUBSCRIBED_DEPARTMENTS: frozenset[str] = frozenset(ALLOWED_EVENT_TYPES_BY_DEPARTMENT)

# The event types the engine is authorized to act on — the REAL wire `type`
# strings from the versioned schemas (schemas/events/sales.py, creative.py),
# each also present in EVENT_REGISTRY. Flattened from the table above so the two
# can never disagree.
SUBSCRIBED_EVENT_TYPES: list[str] = sorted(
    event_type
    for types in ALLOWED_EVENT_TYPES_BY_DEPARTMENT.values()
    for event_type in types
)

# NOTE: the `SUBSCRIBED_STREAMS` alias that used to sit here is GONE, as ADR-0005
# directed. It aliased the event-type list as if those were Redis stream keys;
# stream keys are `evt:{tenant}:{department}`, never event-type names, so it
# addressed streams that do not exist on the live bus. The consumer now derives
# its subscriptions from SUBSCRIBED_DEPARTMENTS and lets the bus build the key.
# Do not reintroduce it: nothing outside a stream-key builder should name a
# stream, and the only stream-key builder is `events/bus.py:stream_name`.

# STAGE 5 (CONFLICT): payload keys that signal an intent to approve vs reject.
# A payload carrying at least one key from EACH set is internally contradictory
# (a proposal that both approves and rejects) and is deferred to a human.
APPROVAL_SIGNAL_KEYS: frozenset[str] = frozenset(
    {
        "approve",
        "approved",
        "approval",
        "approval_signals",
        "auto_approve",
        "greenlight",
    }
)
REJECTION_SIGNAL_KEYS: frozenset[str] = frozenset(
    {
        "reject",
        "rejected",
        "rejection",
        "rejection_signals",
        "veto",
        "block",
        "halt",
    }
)

# STAGE 6 (HITL_GATE): a HIGH-risk action whose upside is below this opportunity
# score is not auto-decided — a human confirms.
HITL_HIGH_RISK_OPPORTUNITY_FLOOR: float = 60.0

# 3×3 grid: (RiskBand, opportunity_bucket) → DecisionOutcome
# Opportunity buckets: LOW = 0–40, MED = 41–70, HIGH = 71–100
# Logic: high risk only auto-approves when opportunity is very high;
# critical risk always defers to human; low risk auto-approves unless opportunity
# is so low it's not worth the operational cost (escalate for review).
DECISION_MATRIX: dict[tuple[RiskBand, str], DecisionOutcome] = {
    (RiskBand.LOW,  "LOW"):  DecisionOutcome.APPROVED,
    (RiskBand.LOW,  "MED"):  DecisionOutcome.APPROVED,
    (RiskBand.LOW,  "HIGH"): DecisionOutcome.APPROVED,
    (RiskBand.MED,  "LOW"):  DecisionOutcome.DEFERRED_TO_HUMAN,
    (RiskBand.MED,  "MED"):  DecisionOutcome.APPROVED,
    (RiskBand.MED,  "HIGH"): DecisionOutcome.APPROVED,
    (RiskBand.HIGH, "LOW"):  DecisionOutcome.REJECTED,
    (RiskBand.HIGH, "MED"):  DecisionOutcome.DEFERRED_TO_HUMAN,
    (RiskBand.HIGH, "HIGH"): DecisionOutcome.DEFERRED_TO_HUMAN,
}

AUDIT_EVENT_TYPE_PREFIX = "decision_engine.audit"

MAX_EVALUATION_TIMEOUT_SECONDS = 30


def opportunity_bucket(opportunity_score: float) -> str:
    """Map a 0–100 opportunity score to its bucket label."""
    if opportunity_score <= 40:
        return "LOW"
    if opportunity_score <= 70:
        return "MED"
    return "HIGH"
