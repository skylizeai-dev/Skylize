"""Memory domain schemas — MemoryScope and MemoryEntry.

These are the boundary types moved between the DAL port, the Memory service,
and the AgentRunner. No driver imports here — safe to import from any layer.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MemoryScope(BaseModel):
    """Filters that constrain which memory entries are visible to a query."""

    model_config = ConfigDict(extra="forbid")

    org_id: str
    department: Optional[str] = None
    session_id: Optional[UUID] = None
    agent_id: Optional[str] = None


class MemoryEntry(BaseModel):
    """One persisted memory record."""

    model_config = ConfigDict(extra="forbid")

    entry_id: UUID = Field(default_factory=uuid4)
    org_id: str
    agent_id: str
    scope: Optional[str] = None
    department: Optional[str] = None
    session_id: Optional[UUID] = None
    tier: str
    content_text: str = ""
    content_hash: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)
    embedding: Optional[list[float]] = None
    importance_score: float = 1.0
    superseded_by: Optional[UUID] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by_agent: Optional[str] = None

    @model_validator(mode="after")
    def _compute_hash(self) -> "MemoryEntry":
        if not self.content_hash:
            object.__setattr__(
                self,
                "content_hash",
                hashlib.sha256(self.content_text.encode()).hexdigest(),
            )
        return self


class ProvenanceEntry(BaseModel):
    """One provenance record on a deduplicated memory fact.

    Appended each time an identical (org_id, namespace, fact_hash) is written,
    so a collapsed fact carries the full list of who reinforced it and when.
    Stored as a JSONB array element on memory_records.provenance.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    agent_id: str
    ts: datetime
    source_correlation_id: UUID


class MemoryWriteOutcome(BaseModel):
    """Result of a dedup-aware write.

    ``created`` is True on a dedup MISS (a new row was inserted →
    ``memory.fact_recorded``) and False on a HIT (provenance appended +
    importance reinforced → ``memory.fact_reinforced``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: UUID
    fact_hash: str
    namespace: str
    created: bool
    provenance_count: int
    importance_score: float


class MemoryWriteRequest(BaseModel):
    """Validated write intent crossing into the memory write service.

    Callers supply raw content; canonicalization and hashing happen inside the
    service — callers must not pre-hash.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    org_id: str
    namespace: str
    tier: str  # episodic | semantic | procedural | org
    content_text: str = Field(min_length=1)
    agent_id: str
    source_event_id: UUID
    correlation_id: UUID
