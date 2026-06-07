"""
IF-EVENT: the EventBus port and stream-naming conventions.

Foundation scope: the `EventBus` Protocol (the seam every producer/consumer
codes against) and the canonical Redis Streams naming. The Redis adapter,
archiver, and DLQ machinery are implemented in Sprint 1's runtime work; the port
here is what keeps them swappable (event_driven_architecture.md §2).
"""

from __future__ import annotations

from .bus import EventBus, consumer_group, dlq_name, stream_name

__all__ = ["EventBus", "stream_name", "dlq_name", "consumer_group"]
