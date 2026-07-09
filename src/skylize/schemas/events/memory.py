"""MemoryEvent — `category=memory` (event_driven_architecture.md §5).

Memory writes are event-sourced: agents emit `memory.write_requested`; the
Memory service commits and emits `memory.committed` / `memory.embedding_indexed`.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ..base import BaseEvent, EventCategory


class MemoryWriteRequested(BaseEvent):
    category: Literal[EventCategory.MEMORY] = EventCategory.MEMORY
    type: Literal["memory.write_requested"] = "memory.write_requested"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        namespace: str
        tier: str  # 'episodic' | 'semantic' | 'procedural' | 'org'
        content_text: str

    payload: Payload


class MemoryCommitted(BaseEvent):
    category: Literal[EventCategory.MEMORY] = EventCategory.MEMORY
    type: Literal["memory.committed"] = "memory.committed"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        record_id: UUID
        namespace: str
        content_hash: str

    payload: Payload


class MemoryEmbeddingIndexed(BaseEvent):
    category: Literal[EventCategory.MEMORY] = EventCategory.MEMORY
    type: Literal["memory.embedding_indexed"] = "memory.embedding_indexed"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        record_id: UUID
        namespace: str
        vector_id: str

    payload: Payload


class MemoryRecallServed(BaseEvent):
    category: Literal[EventCategory.MEMORY] = EventCategory.MEMORY
    type: Literal["memory.recall_served"] = "memory.recall_served"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        namespace: str
        query_hash: str
        result_count: int
        confidence: float

    payload: Payload


class MemoryInvalidated(BaseEvent):
    category: Literal[EventCategory.MEMORY] = EventCategory.MEMORY
    type: Literal["memory.invalidated"] = "memory.invalidated"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        record_id: UUID
        superseded_by: UUID | None = None

    payload: Payload


class MemoryFactRecorded(BaseEvent):
    """A new fact was committed (ON CONFLICT created a fresh record)."""

    category: Literal[EventCategory.MEMORY] = EventCategory.MEMORY
    type: Literal["memory.fact_recorded"] = "memory.fact_recorded"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        fact_id: UUID
        fact_hash: str
        namespace: str
        tier: str
        agent_id: str

    payload: Payload


class MemoryFactReinforced(BaseEvent):
    """An existing fact was re-observed: provenance appended, importance bumped."""

    category: Literal[EventCategory.MEMORY] = EventCategory.MEMORY
    type: Literal["memory.fact_reinforced"] = "memory.fact_reinforced"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        fact_id: UUID
        fact_hash: str
        namespace: str
        provenance_count: int
        new_importance_score: float
        agent_id: str

    payload: Payload
