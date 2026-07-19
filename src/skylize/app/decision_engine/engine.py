"""
The Decision Engine consumer/producer (decision_engine.md §2, §3).

Per environment, exactly one engine emits terminal `decision.*` events, selected
by `SKYLIZE_DECISION_ENGINE`. This is the inline engine; it
consumes proposal-bearing business events (`creative.*`, `sales.*`) and human
verdicts (`governance.human_approval_received`), runs each proposal through the
six-stage `DecisionEvaluator`, and projects the verdict onto the wire schema:

    decision.evaluated            (the evaluation record, always)
    decision.conflict_detected    (+ conflict_resolved when a rival was beaten)
    decision.approved | rejected | deferred_to_human   (exactly one terminal)

Every decision also mirrors `audit.action_recorded`. Delivery is at-least-once,
so the engine is idempotent on `event_id` via a `ProcessedEventStore`: the same
event always yields the same single decision.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from ...config import Settings
from ...contracts.registry import AgentRegistry
from ...dal.memory import InMemoryCapitalRepository, InMemoryProcessedEventStore
from ...dal.ports import CapitalRepository, ProcessedEventStore
from ...events.bus import EventBus, consumer_group
from ...events.router import EventRouter
from ...schemas.base import BaseEvent
from ...schemas.events.decision import (
    DecisionApproved,
    DecisionConflictDetected,
    DecisionConflictResolved,
    DecisionDeferredToHuman,
    DecisionEvaluated,
    DecisionRejected,
)
from ...schemas.events.governance import GovernanceHumanApprovalReceived
from ..audit.service import AuditService
from .evaluator import DecisionEvaluator
from .events import DecisionProposal, DecisionResult, hitl_id_for

log = logging.getLogger("skylize.decision_engine")

# Department channels carrying proposals + human verdicts the engine watches.
DEFAULT_DEPARTMENTS: tuple[str, ...] = ("creative", "growth", "governance")

# Map a terminal outcome to the audit `result` vocabulary (audit ports).
_AUDIT_RESULT = {
    "approved": "success",
    "rejected": "denied",
    "deferred_to_human": "escalated",
}


class DecisionEngine:
    def __init__(
        self,
        event_bus: EventBus,
        authority_registry: AgentRegistry,
        audit_service: AuditService,
        settings: Settings,
        *,
        capital: CapitalRepository | None = None,
        processed: ProcessedEventStore | None = None,
        departments: tuple[str, ...] = DEFAULT_DEPARTMENTS,
    ) -> None:
        self._bus = event_bus
        self._registry = authority_registry
        self._audit = audit_service
        self._settings = settings
        self._capital: CapitalRepository = capital or InMemoryCapitalRepository()
        self._processed: ProcessedEventStore = processed or InMemoryProcessedEventStore()
        self._evaluator = DecisionEvaluator(registry=authority_registry, capital=self._capital)
        self._departments = departments
        self._routers: list[EventRouter] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False

    # -- accessors (seed budgets / inspect in bootstrap + tests) ------------
    @property
    def evaluator(self) -> DecisionEvaluator:
        return self._evaluator

    @property
    def capital(self) -> CapitalRepository:
        return self._capital

    # -- lifecycle ----------------------------------------------------------
    async def start(self, subscriptions: list[tuple[str, str]] | None = None) -> None:
        """Begin consuming. Each subscription is an (org_id, department) pair;
        production wires one per provisioned tenant × watched department."""
        self._running = True
        for org_id, department in subscriptions or []:
            self.subscribe(org_id, department)

    def subscribe(self, tenant_id: str, department: str | None = None) -> None:
        """Spawn consumer task(s) for a tenant (all watched departments if None)."""
        departments = [department] if department is not None else list(self._departments)
        group = consumer_group("decision_engine")
        for dept in departments:
            router = EventRouter(
                self._bus,
                group=group,
                consumer=f"de-{tenant_id}-{dept}",
                dlq_after_retries=self._settings.dlq_after_retries,
            )
            router.on_event(self._handle_event)
            self._routers.append(router)
            self._tasks.append(
                asyncio.create_task(router.run(tenant_id=tenant_id, department=dept))
            )

    async def stop(self) -> None:
        """Graceful shutdown: stop routers and cancel their consume tasks."""
        self._running = False
        for router in self._routers:
            router.stop()
        for task in self._tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        self._routers.clear()

    # -- core dispatch ------------------------------------------------------
    async def _handle_event(self, event: BaseEvent) -> None:
        """Dispatch one consumed event to the evaluator (or HITL resume)."""
        if isinstance(event, GovernanceHumanApprovalReceived):
            await self._resume_from_human(event)
            return

        proposal = DecisionProposal.from_event(event)
        if proposal is None:
            return  # not a decision-bearing event — ignore

        key = str(event.event_id)
        if await self._processed.is_processed(key, org_id=event.tenant_id):
            return  # idempotent: this event was already decided

        result = await self._evaluator.evaluate(proposal)
        await self._emit(proposal, result)
        await self._processed.mark_processed(key, result.outcome, org_id=event.tenant_id)

    # -- emission -----------------------------------------------------------
    async def _emit(self, proposal: DecisionProposal, result: DecisionResult) -> None:
        # The evaluation record always precedes the terminal outcome.
        await self._bus.publish(
            DecisionEvaluated(
                tenant_id=proposal.org_id,
                partition_key=proposal.partition_key,
                department="decision",
                governance_token_id=proposal.governance_token_id,
                causation_id=proposal.source_event_id,
                correlation_id=proposal.correlation_id,
                payload=DecisionEvaluated.Payload(
                    decision_id=result.decision_id,
                    proposing_agent=result.proposing_agent,
                    action_kind=result.action_kind,
                    stages_completed=result.stages_completed,
                    policy_version=result.policy_version,
                ),
            )
        )

        for conflict in result.conflicts:
            await self._bus.publish(
                DecisionConflictDetected(
                    tenant_id=proposal.org_id,
                    partition_key=proposal.partition_key,
                    department="decision",
                    correlation_id=proposal.correlation_id,
                    causation_id=proposal.source_event_id,
                    payload=DecisionConflictDetected.Payload(
                        partition_key=conflict.partition_key,
                        proposal_ids=conflict.proposal_ids,
                    ),
                )
            )
            if conflict.winning_proposal_id is not None:
                await self._bus.publish(
                    DecisionConflictResolved(
                        tenant_id=proposal.org_id,
                        partition_key=proposal.partition_key,
                        department="decision",
                        correlation_id=proposal.correlation_id,
                        causation_id=proposal.source_event_id,
                        payload=DecisionConflictResolved.Payload(
                            partition_key=conflict.partition_key,
                            winning_proposal_id=conflict.winning_proposal_id,
                            rule_applied=conflict.rule_applied,
                        ),
                    )
                )

        if result.outcome == "approved":
            await self._bus.publish(
                DecisionApproved(
                    tenant_id=proposal.org_id,
                    partition_key=proposal.partition_key,
                    department="decision",
                    governance_token_id=proposal.governance_token_id,
                    causation_id=proposal.source_event_id,
                    correlation_id=proposal.correlation_id,
                    payload=DecisionApproved.Payload(
                        decision_id=result.decision_id,
                        action_kind=result.action_kind,
                        approved_scope={
                            "agent": result.proposing_agent,
                            "department": proposal.department,
                            "partition_key": proposal.partition_key,
                        },
                    ),
                )
            )
        elif result.outcome == "rejected":
            await self._bus.publish(
                DecisionRejected(
                    tenant_id=proposal.org_id,
                    partition_key=proposal.partition_key,
                    department="decision",
                    governance_token_id=proposal.governance_token_id,
                    causation_id=proposal.source_event_id,
                    correlation_id=proposal.correlation_id,
                    payload=DecisionRejected.Payload(
                        decision_id=result.decision_id,
                        action_kind=result.action_kind,
                        stage_rejected_at=result.stage_failed_at or "unknown",
                        reasons=result.reasons,
                        policy_version=result.policy_version,
                    ),
                )
            )
        else:  # deferred_to_human
            await self._bus.publish(
                DecisionDeferredToHuman(
                    tenant_id=proposal.org_id,
                    partition_key=proposal.partition_key,
                    department="decision",
                    governance_token_id=proposal.governance_token_id,
                    causation_id=proposal.source_event_id,
                    correlation_id=proposal.correlation_id,
                    payload=DecisionDeferredToHuman.Payload(
                        decision_id=result.decision_id,
                        hitl_id=hitl_id_for(proposal.proposal_id),
                        trigger_reason=result.hitl_trigger or "unspecified",
                        routed_to=result.routed_to or "human_owner",
                    ),
                )
            )

        await self._audit.record(
            org_id=proposal.org_id,
            correlation_id=proposal.correlation_id,
            action_type=f"decision.{result.outcome}",
            result=_AUDIT_RESULT[result.outcome],
            source_agent_id=result.proposing_agent or None,
            authority_level=result.authority_level,
            governance_token_id=proposal.governance_token_id,
            causation_id=proposal.source_event_id,
            partition_key=proposal.partition_key,
            inputs={"action_kind": result.action_kind, "stages": result.stages_completed},
            outputs={"reasons": result.reasons, "score": result.score.value if result.score else None},
            result_reason="; ".join(result.reasons) or None,
        )

    # -- HITL resume --------------------------------------------------------
    async def _resume_from_human(self, event: GovernanceHumanApprovalReceived) -> None:
        """A human verdict resumes a paused decision into its terminal outcome."""
        key = f"hitl:{event.payload.decision_id}"
        if await self._processed.is_processed(key, org_id=event.tenant_id):
            return  # already resumed
        p = event.payload
        if p.approved:
            await self._bus.publish(
                DecisionApproved(
                    tenant_id=event.tenant_id,
                    partition_key=event.partition_key,
                    department="decision",
                    causation_id=event.event_id,
                    correlation_id=event.correlation_id,
                    payload=DecisionApproved.Payload(
                        decision_id=p.decision_id,
                        action_kind="human_resumed",
                        approved_scope={"decided_by": p.decided_by, "resumed": "true"},
                    ),
                )
            )
            outcome = "approved"
        else:
            await self._bus.publish(
                DecisionRejected(
                    tenant_id=event.tenant_id,
                    partition_key=event.partition_key,
                    department="decision",
                    causation_id=event.event_id,
                    correlation_id=event.correlation_id,
                    payload=DecisionRejected.Payload(
                        decision_id=p.decision_id,
                        action_kind="human_resumed",
                        stage_rejected_at="hitl_gate",
                        reasons=[p.reason or "human_rejected"],
                        policy_version=None,
                    ),
                )
            )
            outcome = "rejected"

        await self._audit.record(
            org_id=event.tenant_id,
            correlation_id=event.correlation_id,
            action_type=f"decision.{outcome}",
            result=_AUDIT_RESULT[outcome],
            causation_id=event.event_id,
            partition_key=event.partition_key,
            result_reason=f"human_resume by {p.decided_by}: {p.reason or outcome}",
        )
        await self._processed.mark_processed(key, outcome, org_id=event.tenant_id)
