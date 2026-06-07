"""
The invariant event envelope and the request context.

`BaseEvent` is the frozen envelope every event crossing `IF-EVENT` inherits
(event_driven_architecture.md §3). `RequestContext` is the short-lived, signed
identity derived at the edge from a verified OIDC JWT
(system_boundaries.md §5.2).

Nothing here imports another skylize package — this is a leaf module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

# Canonical authority levels — IDENTICAL to agent_governance.md §2 and the
# AuthorityLevel alias in contracts/base.py. Defined here too so schemas stay a
# leaf package (no import from contracts/).
AuthorityLevelLiteral = Literal["executive", "vp", "director", "manager", "worker"]


class EventCategory(str, Enum):
    """The six top-level event categories. This taxonomy is closed."""

    CREATIVE = "creative"
    SALES = "sales"
    MEMORY = "memory"
    DECISION = "decision"
    GOVERNANCE = "governance"
    AUDIT = "audit"


class BaseEvent(BaseModel):
    """Invariant envelope for every event crossing IF-EVENT.

    The envelope is frozen and `extra="forbid"`; concrete events extend it with
    a typed, independently-versioned `payload`. The bus stamps delivery metadata
    (`redelivery_count`); the publisher never sets it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Identity & versioning
    event_id: UUID = Field(default_factory=uuid4)
    schema_version: str = Field(..., pattern=r"^\d+\.\d+$")  # MAJOR.MINOR, e.g. "1.0"
    category: EventCategory
    type: str  # dotted verb-phrase, e.g. "creative.hooks_generated"

    # Routing & ordering
    tenant_id: str  # org_id; partitions every stream
    partition_key: str  # ordering key (event_driven_architecture.md §8)
    department: str  # owning department channel

    # Provenance (links to governance & agents)
    source_agent_id: str | None = None
    authority_level: AuthorityLevelLiteral | None = None
    governance_token_id: UUID | None = None  # which token authorized this
    causation_id: UUID | None = None  # the event that caused this one
    correlation_id: UUID  # ties a whole workflow together

    # Timing
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Delivery metadata (set by the bus, not the publisher)
    redelivery_count: int = 0


class RequestContext(BaseModel):
    """Short-lived, signed internal identity derived at the edge.

    Internal services trust this, never the raw IdP token. TTL is enforced by
    the gateway (≤5 min) — security_architecture.md §3.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    org_id: str
    user_id: str
    roles: list[str]
    correlation_id: UUID = Field(default_factory=uuid4)
    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
