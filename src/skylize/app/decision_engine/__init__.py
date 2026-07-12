"""
The Decision Engine — turns agent *intent* into authorized *outcomes*.

The only component permitted to emit terminal `decision.*` events. It consumes
proposals off the bus, runs them through six deterministic evaluation stages
(authority → policy → scoring → capital → conflict → HITL), and emits exactly
one terminal outcome per proposal, mirrored to the audit trail.

Public surface:
  - `DecisionEngine`     — the bus consumer/producer (lifecycle + dispatch)
  - `DecisionEvaluator`  — the pure six-stage decision logic
  - `DecisionProposal` / `DecisionResult` / `DecisionScore` / `Conflict`
"""

from __future__ import annotations

from .engine import DEFAULT_DEPARTMENTS, DecisionEngine
from .evaluator import POLICY_VERSION, DecisionEvaluator
from .events import (
    Conflict,
    DecisionProposal,
    DecisionResult,
    DecisionScore,
    decision_id_for,
    hitl_id_for,
)

# Alias used by external callers that reference the consumer role explicitly.
DecisionEngineConsumer = DecisionEngine

__all__ = [
    "DecisionEngine",
    "DecisionEngineConsumer",
    "DEFAULT_DEPARTMENTS",
    "DecisionEvaluator",
    "POLICY_VERSION",
    "DecisionProposal",
    "DecisionResult",
    "DecisionScore",
    "Conflict",
    "decision_id_for",
    "hitl_id_for",
]
