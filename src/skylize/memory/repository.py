"""
Memory write service — the dedup-aware write contract.

A write is collapsed on the canonical content hash: identical (namespace,
content) writes become one row whose `provenance` list grows by one entry per
write and whose `importance_score` is reinforced with time decay. The actual
INSERT/UPSERT is delegated to a `MemoryWritePort` (the DAL owns SQL); this layer
owns canonicalization, hashing, and emitting the typed events.

Boundary notes:
  - No driver import (import-linter forbids a DB driver in `memory`); we depend on
    the `MemoryWritePort` *Protocol*, not on any asyncpg implementation.
  - We depend on the `EventBus` port only — not on `app` — so there is no import
    cycle with the application layer. The audit mirror is published as a normal
    `audit.action_recorded` event on the same sanctioned channel (every
    state-changing action emits an AuditEvent with the correlation_id — spine
    invariant).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..dal.ports import MemoryWritePort
from ..events.bus import EventBus
from ..schemas.events.audit import AuditActionRecorded
from ..schemas.events.memory import MemoryFactRecorded, MemoryFactReinforced
from ..schemas.memory import MemoryWriteOutcome, MemoryWriteRequest, ProvenanceEntry
from .dedup import (
    DEFAULT_HALF_LIFE_SECONDS,
    DEFAULT_REINFORCEMENT,
    canonicalize_content,
    compute_fact_hash,
)


class MemoryWriteService:
    """Existence-checked memory writes with provenance accumulation."""

    def __init__(
        self,
        repo: MemoryWritePort,
        bus: EventBus,
        *,
        half_life_seconds: float = DEFAULT_HALF_LIFE_SECONDS,
        reinforcement: float = DEFAULT_REINFORCEMENT,
    ) -> None:
        self._repo = repo
        self._bus = bus
        self._half_life_seconds = half_life_seconds
        self._reinforcement = reinforcement

    async def write(self, request: MemoryWriteRequest) -> MemoryWriteOutcome:
        """Insert-or-reinforce one fact; emit the fact event + an audit mirror.

        Returns the outcome (with ``created`` distinguishing a fresh INSERT from a
        reinforced HIT). Idempotency: the write collapses on ``fact_hash``, and the
        appended provenance entry carries ``event_id`` so a redelivered identical
        event can be deduped downstream.
        """
        fact_hash = compute_fact_hash(request.namespace, request.content_text)
        content_canonical = canonicalize_content(request.content_text)

        provenance_entry = ProvenanceEntry(
            event_id=request.source_event_id,
            agent_id=request.agent_id,
            ts=datetime.now(timezone.utc),
            source_correlation_id=request.correlation_id,
        )

        outcome = await self._repo.upsert_fact(
            org_id=request.org_id,
            namespace=request.namespace,
            tier=request.tier,
            fact_hash=fact_hash,
            content_text=content_canonical,
            provenance_entry=provenance_entry,
            created_by_agent=request.agent_id,
            half_life_seconds=self._half_life_seconds,
            reinforcement=self._reinforcement,
        )

        await self._emit_fact_event(request, outcome)
        await self._emit_audit(request, outcome)
        return outcome

    async def _emit_fact_event(
        self, request: MemoryWriteRequest, outcome: MemoryWriteOutcome
    ) -> None:
        partition_key = f"memory:{request.namespace}:{outcome.fact_hash}"
        if outcome.created:
            event = MemoryFactRecorded(
                tenant_id=request.org_id,
                partition_key=partition_key,
                department="memory",
                source_agent_id=request.agent_id,
                correlation_id=request.correlation_id,
                causation_id=request.source_event_id,
                payload=MemoryFactRecorded.Payload(
                    fact_id=outcome.record_id,
                    fact_hash=outcome.fact_hash,
                    namespace=outcome.namespace,
                    tier=request.tier,
                    agent_id=request.agent_id,
                ),
            )
            await self._bus.publish(event)
        else:
            reinforced = MemoryFactReinforced(
                tenant_id=request.org_id,
                partition_key=partition_key,
                department="memory",
                source_agent_id=request.agent_id,
                correlation_id=request.correlation_id,
                causation_id=request.source_event_id,
                payload=MemoryFactReinforced.Payload(
                    fact_id=outcome.record_id,
                    fact_hash=outcome.fact_hash,
                    namespace=outcome.namespace,
                    provenance_count=outcome.provenance_count,
                    new_importance_score=outcome.importance_score,
                    agent_id=request.agent_id,
                ),
            )
            await self._bus.publish(reinforced)

    async def _emit_audit(
        self, request: MemoryWriteRequest, outcome: MemoryWriteOutcome
    ) -> None:
        action = "memory.fact_recorded" if outcome.created else "memory.fact_reinforced"
        audit = AuditActionRecorded(
            tenant_id=request.org_id,
            partition_key=str(request.correlation_id),
            department="audit",
            source_agent_id=request.agent_id,
            correlation_id=request.correlation_id,
            causation_id=request.source_event_id,
            payload=AuditActionRecorded.Payload(
                action_type=action,
                outputs_hash=outcome.fact_hash,
                result="success",
                result_reason=(
                    f"provenance_count={outcome.provenance_count}"
                    f" importance={outcome.importance_score:.4f}"
                ),
            ),
        )
        await self._bus.publish(audit)
