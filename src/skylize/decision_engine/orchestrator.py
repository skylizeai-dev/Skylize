"""Decision orchestration — the glue that turns one proposal into one decision.

Chains the three already-tested collaborators in the order the engine runs them
per proposal (decision_flow.md §3):

    pipeline.evaluate(context)          # six-stage evaluation → DecisionResult
      → publisher.publish_outcome(result)   # decisions + decision_outbox (one tx)
      → hitl_writer.write_escalation(...)    # only on DEFERRED_TO_HUMAN / ESCALATED

``process`` has the exact ``Callable[[DecisionContext], Awaitable[DecisionResult]]``
shape ``DecisionEngineConsumer`` takes as its ``pipeline_fn``, so the worker wires
the two in one line (``worker.build_consumer``)::

    consumer = DecisionEngineConsumer(bus, settings, orchestrator.process)

This wrapper owns neither transport (the consumer supplies events) nor the Redis
relay (the OutboxPoller owns that); it only composes evaluation, persistence, and
escalation. Exceptions propagate unchanged so the consumer's retry/DLQ path
engages, and the pipeline's own timeout plus the publisher's idempotent
(``ON CONFLICT``) writes make a redelivery safe to re-run.

Safe to re-run, and now actually re-run: the shared Redis adapter reclaims stalled
PEL entries (redis_adapter.RedisEventBus._reclaim), so a proposal whose evaluation
raises is redelivered once its idle window elapses and retried until the router's
budget routes it to the DLQ. Propagating rather than swallowing is what makes that
path engage at all.
"""

from __future__ import annotations

import logging

from .hitl_writer import HITLQueueWriter
from .models import DecisionContext, DecisionOutcome, DecisionResult
from .pipeline import EvaluationPipeline, hitl_id_for
from .publisher import DecisionEventPublisher

log = logging.getLogger(__name__)

# Outcomes that route a proposal to a human — the ONLY ones that write a
# hitl_queue escalation record. APPROVED/REJECTED are terminal without HITL.
_ESCALATION_OUTCOMES: frozenset[DecisionOutcome] = frozenset(
    {DecisionOutcome.DEFERRED_TO_HUMAN, DecisionOutcome.ESCALATED}
)


class DecisionOrchestrator:
    """Runs evaluate → persist/enqueue → (escalate) for one ``DecisionContext``."""

    def __init__(
        self,
        pipeline: EvaluationPipeline,
        publisher: DecisionEventPublisher,
        hitl_writer: HITLQueueWriter,
    ) -> None:
        self._pipeline = pipeline
        self._publisher = publisher
        self._hitl_writer = hitl_writer

    async def process(self, context: DecisionContext) -> DecisionResult:
        """Evaluate one proposal and durably record its outcome.

        1. Evaluate through the six-stage pipeline.
        2. Persist the decision + enqueue its event (transactional outbox).
        3. On DEFERRED_TO_HUMAN / ESCALATED, write a hitl_queue escalation —
           skipped if a pending record already exists for this event + tenant
           (idempotent on redelivery).

        Returns the ``DecisionResult``. Raising, not swallowing, is deliberate:
        the consumer translates an exception into a retry / DLQ.
        """
        result = await self._pipeline.evaluate(context)

        # Minted once, upstream of both writers, so the decision.deferred_to_human
        # event payload and the hitl_queue row always agree on the ticket id.
        # Deterministic (uuid5 from decision_id) so a redelivery reconstructs the
        # same hitl_id rather than minting a duplicate ticket.
        hitl_id = (
            hitl_id_for(result.decision_id)
            if result.outcome in _ESCALATION_OUTCOMES
            else None
        )

        await self._publisher.publish_outcome(result, hitl_id)

        if result.outcome in _ESCALATION_OUTCOMES:
            already = await self._hitl_writer.check_duplicate_escalation(
                context.event_id, context.tenant_id
            )
            if already:
                log.info(
                    "decision_escalation_deduplicated",
                    extra={
                        "decision_id": result.decision_id,
                        "tenant_id": context.tenant_id,
                        "event_id": context.event_id,
                    },
                )
            else:
                await self._hitl_writer.write_escalation(context, result, hitl_id)

        log.info(
            "decision_processed",
            extra={
                "decision_id": result.decision_id,
                "tenant_id": context.tenant_id,
                "event_id": context.event_id,
                "outcome": result.outcome.value,
            },
        )
        return result


__all__ = ["DecisionOrchestrator"]
