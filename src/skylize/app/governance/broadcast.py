"""
Governance invalidation broadcast — the cross-process propagation seam.

kill_switch_protocol.md §5 requires that engaging a kill (or a revoke/suspend)
is effective on *every* instance, not just the one that handled the control-plane
request. The in-process `GovernanceSnapshot` is the O(1) hot cache; this port is
how a mutation on instance A reaches instances B…N so their caches agree.

This is deliberately NOT the event bus (`IF-EVENT`):
  - it is control-plane, fan-out-to-ALL (every instance must apply it), whereas
    the bus delivers one-of-consumer-group;
  - it must bypass DLQ/retry/ordering machinery — an invalidation is idempotent
    and self-healing (the DB remains the system of record + periodic rehydrate).

The port has two implementations: a Redis Pub/Sub adapter (production) and an
in-process fan-out (`InMemoryGovernanceBroadcast`) used by the `memory` backend
and by multi-instance propagation tests.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from uuid import UUID


class InvalidationKind(str, Enum):
    REVOKE = "revoke"                 # a token_id was revoked
    AGENT_STATE = "agent_state"       # an (agent, org) state changed
    KILL_TENANT = "kill_tenant"       # a tenant kill engaged/disengaged
    KILL_PLATFORM = "kill_platform"   # platform kill engaged/disengaged
    AUTHORITY = "authority"           # a human principal's grants changed


@dataclass(frozen=True, slots=True)
class GovernanceInvalidation:
    """A single snapshot-invalidation message fanned out to all instances."""

    kind: InvalidationKind
    # Populated per-kind (only the relevant fields are set):
    token_id: UUID | None = None
    agent_id: str | None = None
    org_id: str | None = None
    state: str | None = None          # for AGENT_STATE: active|suspended|killed
    engaged: bool | None = None       # for KILL_* : True=engage, False=disengage
    principal_id: str | None = None   # for AUTHORITY: whose grants changed

    def to_json(self) -> str:
        import json

        return json.dumps(
            {
                "kind": self.kind.value,
                "token_id": str(self.token_id) if self.token_id else None,
                "agent_id": self.agent_id,
                "org_id": self.org_id,
                "state": self.state,
                "engaged": self.engaged,
                "principal_id": self.principal_id,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> "GovernanceInvalidation":
        import json

        d = json.loads(raw)
        return cls(
            kind=InvalidationKind(d["kind"]),
            token_id=UUID(d["token_id"]) if d.get("token_id") else None,
            agent_id=d.get("agent_id"),
            org_id=d.get("org_id"),
            state=d.get("state"),
            engaged=d.get("engaged"),
            principal_id=d.get("principal_id"),
        )


InvalidationHandler = Callable[[GovernanceInvalidation], Awaitable[None]]


class GovernanceBroadcast(Protocol):
    """Publish/subscribe of governance invalidations across all instances."""

    async def publish(self, msg: GovernanceInvalidation) -> None:
        """Fan a single invalidation out to every subscribed instance."""
        ...

    async def subscribe(self, handler: InvalidationHandler) -> None:
        """Run the subscriber loop, applying each received invalidation.

        Long-running; intended to be launched as a background task. The handler
        is the Authority's snapshot-mutation applier.
        """
        ...


class InMemoryGovernanceBroadcast:
    """In-process fan-out: every subscriber receives every published message.

    Lets one test process drive multiple Authorities (A/B/C) sharing a single
    broadcast object and assert cross-instance propagation deterministically.
    """

    def __init__(self) -> None:
        self._handlers: list[InvalidationHandler] = []

    async def publish(self, msg: GovernanceInvalidation) -> None:
        # Synchronous fan-out to every other subscriber's handler.
        for handler in list(self._handlers):
            await handler(msg)

    async def subscribe(self, handler: InvalidationHandler) -> None:
        self._handlers.append(handler)
