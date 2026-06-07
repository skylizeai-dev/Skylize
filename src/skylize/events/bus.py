"""
The EventBus port (event_driven_architecture.md §2, §4).

Every agent output, decision, memory mutation, governance action, and audit
record flows through this single sanctioned async channel. The port abstracts
Redis Streams so a future per-department migration to Kafka/NATS is an adapter
swap with no producer/consumer code change.

This module is foundation: the Protocol and naming helpers only. No Redis client
is imported here — the concrete adapter (`redis_adapter.py`) implements this
Protocol and is wired in the runtime work.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..schemas.base import BaseEvent

# ---------------------------------------------------------------------------
# Canonical stream / group naming (event_driven_architecture.md §2)
# ---------------------------------------------------------------------------


def stream_name(tenant_id: str, department: str) -> str:
    """`evt:{tenant}:{department}` — one stream per department channel, per tenant."""
    return f"evt:{tenant_id}:{department}"


def dlq_name(tenant_id: str, department: str) -> str:
    """`dlq:{tenant}:{department}` — paired dead-letter queue."""
    return f"dlq:{tenant_id}:{department}"


def consumer_group(consumer_name: str) -> str:
    """`cg:{consumer_name}` — e.g. `cg:decision_engine`."""
    return f"cg:{consumer_name}"


# ---------------------------------------------------------------------------
# Delivery envelope returned by the bus on consume
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeliveredEvent:
    """A consumed event plus the stream metadata needed to ACK it."""

    stream: str
    message_id: str  # Redis Streams entry id (monotonic within a stream)
    event: BaseEvent


# ---------------------------------------------------------------------------
# The port
# ---------------------------------------------------------------------------


@runtime_checkable
class EventBus(Protocol):
    """The single sanctioned internal async channel.

    Invariants the concrete adapter must uphold (event_driven_architecture.md §13):
      - validate against a versioned Pydantic schema or route to DLQ — never drop;
      - stamp `occurred_at`, enforce `correlation_id` and `partition_key`;
      - per-`partition_key` ordering; at-least-once delivery (consumers idempotent
        on `event_id`).
    """

    async def publish(self, event: BaseEvent) -> str:
        """Validate and append the event to `evt:{tenant}:{department}`.

        Returns the assigned stream message id. Invalid events are routed to the
        DLQ with an `audit.schema_rejected` mirror, never silently dropped.
        """
        ...

    def consume(
        self,
        *,
        tenant_id: str,
        department: str,
        group: str,
        consumer: str,
    ) -> AsyncIterator[DeliveredEvent]:
        """Stream events for a department to one consumer in a consumer group."""
        ...

    async def ack(self, delivered: DeliveredEvent, *, group: str) -> None:
        """Acknowledge successful processing (removes from the group PEL)."""
        ...

    async def to_dlq(self, delivered: DeliveredEvent, *, reason: str) -> None:
        """Route a poisoned/failed message to its department DLQ, audited."""
        ...
