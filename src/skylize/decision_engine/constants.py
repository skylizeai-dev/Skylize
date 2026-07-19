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
}

# The departments the engine serves — STAGE 1 (AUTHORITY), pipeline.py.
ALLOWED_DEPARTMENTS: frozenset[str] = frozenset(ALLOWED_EVENT_TYPES_BY_DEPARTMENT)

# The departments the consumer subscribes to — one `evt:{tenant}:{department}`
# stream per entry, per the bus's routing key (events/bus.py:27). Identical to
# ALLOWED_DEPARTMENTS *by construction*, not by coincidence: the engine may only
# receive what it is authorized to act on, and vice versa.
SUBSCRIBED_DEPARTMENTS: frozenset[str] = ALLOWED_DEPARTMENTS

# The event types the engine is authorized to act on — the REAL wire `type`
# strings from the versioned schemas (schemas/events/sales.py, creative.py),
# each also present in EVENT_REGISTRY. Flattened from the table above so the two
# can never disagree.
SUBSCRIBED_EVENT_TYPES: list[str] = sorted(
    event_type
    for types in ALLOWED_EVENT_TYPES_BY_DEPARTMENT.values()
    for event_type in types
)

# Redis stream keys the consumer reads.
#
# TRANSPORT MISMATCH (deferred rebuild): the live RedisEventBus keys every event
# as `evt:{tenant}:{department}`, per-tenant (events/bus.py) — NOT by event-type
# name, and the engine does not know the tenant set a priori. Consuming the real
# bus therefore requires rebuilding DecisionEngineConsumer onto the EventBus port
# with a per-(org, department) subscription, exactly as the canonical inline
# engine already does (app/decision_engine/engine.py). Until that lands the
# consumer is NOT started at the composition root; these logical identifiers only
# stand in for the still-isolated consumer unit tests. Keying and the AUTHORITY
# allow-list are now separate concerns — this list no longer doubles as the
# event-type vocabulary (see SUBSCRIBED_EVENT_TYPES above).
#
# ADR-0005 directs that this alias be DELETED (not re-pointed) as part of the
# transport rebuild — stream keys are `evt:{tenant}:{department}`, never
# event-type names, so SUBSCRIBED_DEPARTMENTS is what the rebuilt consumer will
# key on. It is left in place here only because deleting it means rewriting
# DecisionEngineConsumer, which is the gated rebuild itself; this change stays
# confined to the vocabulary.
SUBSCRIBED_STREAMS: list[str] = list(SUBSCRIBED_EVENT_TYPES)

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
