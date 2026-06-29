"""The Governance Authority — root of trust for every side effect."""

from __future__ import annotations

from .authority import (
    CIRCUIT_BREAKER_THRESHOLD,
    CONVERGENCE_TRIP_REASON,
    ConvergenceTracker,
    GovernanceAuthority,
    GovernanceDenied,
    compute_action_hash,
)

__all__ = [
    "GovernanceAuthority",
    "GovernanceDenied",
    "CIRCUIT_BREAKER_THRESHOLD",
    "CONVERGENCE_TRIP_REASON",
    "ConvergenceTracker",
    "compute_action_hash",
]
