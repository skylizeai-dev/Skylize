from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import CapitalCheckResult


class DecisionEngineError(Exception):
    """Base exception for all decision engine errors."""


class AuthorityViolation(DecisionEngineError):
    """Agent authority level insufficient for the requested action."""


class OPAPolicyDenied(DecisionEngineError):
    """OPA evaluated the policy and returned a deny."""

    def __init__(self, policy_path: str, denial_reason: str) -> None:
        self.policy_path = policy_path
        self.denial_reason = denial_reason
        super().__init__(f"OPA policy {policy_path!r} denied: {denial_reason}")


class CapitalCeilingExceeded(DecisionEngineError):
    """Requested spend exceeds the applicable capital ceiling."""

    def __init__(self, result: CapitalCheckResult) -> None:
        self.result = result
        super().__init__(result.reason)


class ConflictDetected(DecisionEngineError):
    """Competing proposals with overlapping partition keys detected."""

    def __init__(self, conflict_keys: list[str]) -> None:
        self.conflict_keys = conflict_keys
        super().__init__(f"Conflict on keys: {conflict_keys}")


class EvaluationTimeout(DecisionEngineError):
    """Decision evaluation exceeded the maximum allowed time."""
