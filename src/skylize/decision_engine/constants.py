from __future__ import annotations

from .models import DecisionOutcome, RiskBand

SUBSCRIBED_STREAMS: list[str] = [
    "creative.review_requested",
    "sales.proposal_submitted",
    "sales.budget_requested",
]


def _build_allowed_event_types() -> dict[str, frozenset[str]]:
    """Group SUBSCRIBED_STREAMS by their `{department}.` prefix.

    The AUTHORITY stage uses this to assert an inbound event's department is one
    the engine serves and that its `event_type` is one that department is allowed
    to raise.
    """
    grouped: dict[str, set[str]] = {}
    for stream in SUBSCRIBED_STREAMS:
        department = stream.split(".", 1)[0]
        grouped.setdefault(department, set()).add(stream)
    return {dept: frozenset(types) for dept, types in grouped.items()}


# department -> allowed event_type set; derived once from SUBSCRIBED_STREAMS.
ALLOWED_EVENT_TYPES_BY_DEPARTMENT: dict[str, frozenset[str]] = (
    _build_allowed_event_types()
)
ALLOWED_DEPARTMENTS: frozenset[str] = frozenset(ALLOWED_EVENT_TYPES_BY_DEPARTMENT)

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
