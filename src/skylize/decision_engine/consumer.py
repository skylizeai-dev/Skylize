"""Decision Engine consumer — the OPA engine's transport onto the EventBus port.

Rebuilt per ADR-0005 (accepted 2026-07-19). The previous implementation drove
``redis.asyncio`` directly against globally-named, event-type-keyed streams
(``SUBSCRIBED_STREAMS``) that never existed on the live bus: ``RedisEventBus``
keys every event as ``evt:{tenant}:{department}`` and the engine does not know
the tenant set a priori. That made the consumer untestable against a real bus
and unwireable at the composition root.

This is deliberately the SAME transport the canonical inline engine already runs
in production (``app/decision_engine/engine.py``) — not a second one:

  - the ``EventBus`` port, never a Redis client, so the Kafka/NATS swap stays an
    adapter change (event_driven_architecture.md §2);
  - one ``EventRouter`` per (tenant, department) pair, each consuming
    ``evt:{tenant}:{department}`` — subscriptions derive from
    ``SUBSCRIBED_DEPARTMENTS``, the vocabulary table's own projection, so the
    AUTHORITY allow-list and the subscription set cannot drift apart;
  - at-least-once delivery with DLQ after ``redis_max_retries`` attempts, which
    the router owns;
  - idempotency on ``event_id`` through the ``ProcessedEventStore`` port —
    durable on the postgres backend (``decision_processed_events``, migration
    0011), in-memory otherwise. The old Redis ``SETNX`` key is gone with the
    Redis client.

What this consumer adds over the inline engine is only the sink: instead of
evaluating inline it feeds ``pipeline_fn`` — ``DecisionOrchestrator.process``,
which runs the six-stage OPA pipeline and durably records the outcome.

Two filters, not one, and they answer different questions. This module asks "is
this event addressed to the engine at all?" and silently ignores anything else
riding the department channel; the pipeline's AUTHORITY stage then asks "is this
proposal authorized?" and REJECTS what fails. Without the first filter every
unrelated event on ``evt:{tenant}:growth`` would manufacture a spurious REJECTED
decision — the inline engine draws the same line with
``DecisionProposal.from_event(...) is None``.

KNOWN GAP (inherited, deliberate): the router does not XAUTOCLAIM messages
stranded in a dead worker's PEL, so ``redis_idle_time_ms`` is now unused. The
old consumer had a reclaim loop, but only against streams that did not exist.
Matching the proven inline transport was preferred over inventing a third one;
reclaim belongs in the bus adapter, where both engines would gain it at once.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from contextlib import suppress
from datetime import datetime, timezone

from ..dal.memory import InMemoryProcessedEventStore
from ..dal.ports import ProcessedEventStore
from ..events.bus import EventBus
from ..events.router import EventRouter
from ..schemas.base import BaseEvent
from .config import DecisionEngineSettings
from .constants import ALLOWED_EVENT_TYPES_BY_DEPARTMENT, SUBSCRIBED_DEPARTMENTS
from .models import DecisionContext, DecisionResult

log = logging.getLogger(__name__)

# ``DecisionOrchestrator.process`` has exactly this shape.
PipelineFn = Callable[[DecisionContext], Awaitable[DecisionResult]]


class DecisionEngineConsumer:
    """Consumes department channels and feeds each proposal to ``pipeline_fn``."""

    def __init__(
        self,
        bus: EventBus,
        settings: DecisionEngineSettings,
        pipeline_fn: PipelineFn,
        *,
        processed: ProcessedEventStore | None = None,
        departments: Iterable[str] = SUBSCRIBED_DEPARTMENTS,
    ) -> None:
        self._bus = bus
        self._settings = settings
        self._pipeline_fn = pipeline_fn
        self._processed: ProcessedEventStore = processed or InMemoryProcessedEventStore()
        # Sorted so the subscription order — and therefore the consumer names —
        # is stable across restarts rather than following frozenset iteration.
        self._departments: tuple[str, ...] = tuple(sorted(departments))
        self._group = settings.redis_consumer_group
        self._routers: list[EventRouter] = []
        self._tasks: list[asyncio.Task[None]] = []

    # -- lifecycle ----------------------------------------------------------

    async def start(self, subscriptions: Sequence[tuple[str, str]] | None = None) -> None:
        """Begin consuming. Each subscription is an ``(org_id, department)`` pair."""
        for org_id, department in subscriptions or []:
            self.subscribe(org_id, department)

    def subscribe(self, tenant_id: str, department: str | None = None) -> None:
        """Spawn consumer task(s) for a tenant (all served departments if None)."""
        departments = [department] if department is not None else list(self._departments)
        for dept in departments:
            router = EventRouter(
                self._bus,
                group=self._group,
                # Host-qualified so two worker replicas never share a Redis
                # consumer name (each would inherit the other's PEL entries).
                consumer=f"{self._settings.redis_consumer_name}-{tenant_id}-{dept}",
                dlq_after_retries=self._settings.redis_max_retries,
            )
            router.on_event(self._handle_event)
            self._routers.append(router)
            self._tasks.append(
                asyncio.create_task(router.run(tenant_id=tenant_id, department=dept))
            )
            log.info(
                "decision_engine_subscribed",
                extra={"tenant_id": tenant_id, "department": dept, "group": self._group},
            )

    async def run(self, org_ids: Sequence[str]) -> None:
        """Subscribe every ``org × served department`` pair and serve until cancelled.

        The blocking entrypoint a worker process awaits (see ``worker.py``).
        Raises on empty ``org_ids`` rather than idling silently: a dedicated
        consumer process with nothing to consume is a misconfiguration, and a
        worker that looks healthy while proposals pile up unread is worse than
        one that refuses to start.
        """
        if not org_ids:
            raise RuntimeError(
                "DecisionEngineConsumer.run called with no org_ids: set "
                "SKYLIZE_DECISION_ENGINE_ORG_IDS to the tenants this worker serves."
            )
        await self.start(
            [(org_id, dept) for org_id in org_ids for dept in self._departments]
        )
        log.info(
            "decision_engine_consumer_started",
            extra={
                "orgs": len(org_ids),
                "departments": list(self._departments),
                "subscriptions": len(self._tasks),
            },
        )
        try:
            await asyncio.gather(*self._tasks)
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Graceful shutdown: stop routers and cancel their consume tasks."""
        for router in self._routers:
            router.stop()
        for task in self._tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        self._routers.clear()
        log.info("decision_engine_consumer_shutdown")

    # -- dispatch -----------------------------------------------------------

    async def _handle_event(self, event: BaseEvent) -> None:
        """Evaluate one consumed event, or ignore it if it is not ours.

        Exceptions propagate on purpose: the router turns them into a no-ACK
        redelivery and, past ``redis_max_retries``, a DLQ route. The pipeline's
        hard timeout and the publisher's ``ON CONFLICT`` writes make re-running a
        redelivered proposal safe.
        """
        if not _is_addressed_to_engine(event):
            log.debug(
                "decision_engine_event_ignored",
                extra={
                    "event_id": str(event.event_id),
                    "event_type": event.type,
                    "department": event.department,
                },
            )
            return

        key = str(event.event_id)
        if await self._processed.is_processed(key, org_id=event.tenant_id):
            log.debug(
                "decision_engine_duplicate_skipped",
                extra={"event_id": key, "tenant_id": event.tenant_id},
            )
            return

        context = DecisionContext(
            event_id=key,
            tenant_id=event.tenant_id,
            department=event.department,
            event_type=event.type,
            # mode="json" so the payload is JSON-native all the way down: it is
            # POSTed verbatim to OPA (httpx's json= uses the stdlib encoder, which
            # raises on a UUID) and written to hitl_queue.proposal_json.
            payload=event.model_dump(mode="json"),
            received_at=datetime.now(timezone.utc),
        )

        result = await self._pipeline_fn(context)

        # Marked only after the outcome is durably recorded, so a crash mid-flight
        # redelivers rather than silently dropping the proposal.
        await self._processed.mark_processed(
            key, result.outcome.value, org_id=event.tenant_id
        )
        log.info(
            "decision_engine_message_processed",
            extra={
                "event_id": key,
                "tenant_id": event.tenant_id,
                "department": event.department,
                "event_type": event.type,
                "outcome": result.outcome.value,
            },
        )


def _is_addressed_to_engine(event: BaseEvent) -> bool:
    """Is this event one the engine is here to decide on?

    Keyed off the ADR-0005 table directly, so a department gains or loses
    coverage in exactly one place. Note this checks the pairing, not membership
    of two flat lists: an event type is addressed to the engine only on the
    department that owns it.
    """
    return event.type in ALLOWED_EVENT_TYPES_BY_DEPARTMENT.get(
        event.department, frozenset()
    )


__all__ = ["DecisionEngineConsumer", "PipelineFn"]
