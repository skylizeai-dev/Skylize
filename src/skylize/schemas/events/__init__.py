"""
The six event categories (event_driven_architecture.md §5).

Each concrete event is a frozen Pydantic model extending `BaseEvent` with a
typed, independently-versioned `payload`. The `EVENT_REGISTRY` maps every
`type` string to its model class so the bus can resolve and validate inbound
events and the CI contract gate can enumerate them.
"""

from __future__ import annotations

from ..base import BaseEvent
from .audit import (
    AuditAccessDenied,
    AuditActionRecorded,
    AuditDataAccess,
    AuditReplayExecuted,
    AuditSchemaRejected,
)
from .creative import (
    CreativeAssetApproved,
    CreativeAssetRejected,
    CreativeCopyDrafted,
    CreativeHooksGenerated,
    CreativeReviewRequested,
)
from .decision import (
    DecisionApproved,
    DecisionConflictDetected,
    DecisionConflictResolved,
    DecisionDeferredToHuman,
    DecisionEvaluated,
    DecisionRejected,
)
from .governance import (
    GovernanceAgentReinstated,
    GovernanceAgentSuspended,
    GovernanceCircuitBreakerTripped,
    GovernanceKillSwitchDisengaged,
    GovernanceKillSwitchEngaged,
    GovernanceScopeViolation,
    GovernanceTokenIssued,
    GovernanceTokenRevoked,
)
from .memory import (
    MemoryCommitted,
    MemoryEmbeddingIndexed,
    MemoryFactRecorded,
    MemoryFactReinforced,
    MemoryInvalidated,
    MemoryRecallServed,
    MemoryWriteRequested,
)
from .sales import (
    SalesBudgetReallocationProposed,
    SalesCampaignLaunched,
    SalesCampaignProposed,
    SalesLeadEnriched,
    SalesPerformanceIngested,
    SalesSignalDetected,
)

# Authoritative map of event `type` -> model class. Adding a new event type
# requires registering it here; the contract gate asserts every model is
# reachable and round-trips.
EVENT_REGISTRY: dict[str, type[BaseEvent]] = {
    cls.model_fields["type"].default: cls
    for cls in (
        # creative
        CreativeHooksGenerated,
        CreativeCopyDrafted,
        CreativeReviewRequested,
        CreativeAssetApproved,
        CreativeAssetRejected,
        # sales
        SalesLeadEnriched,
        SalesSignalDetected,
        SalesCampaignProposed,
        SalesCampaignLaunched,
        SalesPerformanceIngested,
        SalesBudgetReallocationProposed,
        # memory
        MemoryWriteRequested,
        MemoryCommitted,
        MemoryEmbeddingIndexed,
        MemoryRecallServed,
        MemoryInvalidated,
        MemoryFactRecorded,
        MemoryFactReinforced,
        # decision
        DecisionEvaluated,
        DecisionApproved,
        DecisionRejected,
        DecisionDeferredToHuman,
        DecisionConflictDetected,
        DecisionConflictResolved,
        # governance
        GovernanceTokenIssued,
        GovernanceTokenRevoked,
        GovernanceScopeViolation,
        GovernanceCircuitBreakerTripped,
        GovernanceAgentSuspended,
        GovernanceAgentReinstated,
        GovernanceKillSwitchEngaged,
        GovernanceKillSwitchDisengaged,
        # audit
        AuditActionRecorded,
        AuditAccessDenied,
        AuditDataAccess,
        AuditSchemaRejected,
        AuditReplayExecuted,
    )
}

__all__ = ["EVENT_REGISTRY"]
