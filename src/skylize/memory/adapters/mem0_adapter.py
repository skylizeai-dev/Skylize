"""Mem0 cloud memory adapter.

Satisfies the MemoryAdapter Protocol.  All reads/writes include
org_id + dept_id + session_id as metadata filters so tenant data
never bleeds across boundaries.

Failure policy:
  - retrieve failure  → log error, return []
  - store failure     → log error, raise MemoryWriteError
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

import structlog

from ...errors import MemoryWriteError
from ...schemas.memory import MemoryEntry, MemoryScope

log = structlog.get_logger(__name__)


class Mem0Adapter:
    """Adapter that delegates memory storage to the Mem0 cloud API."""

    def __init__(self, *, api_key: str) -> None:
        try:
            from mem0 import MemoryClient  # type: ignore[import-untyped]
            self._client: Any = MemoryClient(api_key=api_key)
        except ImportError as exc:
            raise RuntimeError(
                "mem0 package is not installed; "
                "add mem0ai to your dependencies or disable Mem0 integration"
            ) from exc

    # ------------------------------------------------------------------
    # MemoryAdapter Protocol
    # ------------------------------------------------------------------

    async def retrieve(self, scope: MemoryScope) -> list[MemoryEntry]:
        try:
            filters = _scope_filters(scope)
            results = self._client.search(
                query="",
                filters=filters,
                limit=20,
            )
            return [_mem0_to_entry(r, scope) for r in (results or [])]
        except Exception as exc:
            log.error(
                "mem0.retrieve.failed",
                org_id=scope.org_id,
                dept_id=scope.department,
                session_id=str(scope.session_id),
                error=str(exc),
            )
            return []

    async def store(self, scope: MemoryScope, entry: MemoryEntry) -> None:
        try:
            user_id = _user_id(scope)
            metadata = {
                "org_id": scope.org_id,
                "dept_id": scope.department,
                "session_id": str(scope.session_id) if scope.session_id else None,
                "agent_id": entry.agent_id,
                "tier": entry.tier,
                **entry.metadata,
            }
            self._client.add(
                entry.content_text,
                user_id=user_id,
                metadata=metadata,
            )
        except Exception as exc:
            log.error(
                "mem0.store.failed",
                org_id=scope.org_id,
                dept_id=scope.department,
                session_id=str(scope.session_id),
                agent_id=entry.agent_id,
                error=str(exc),
            )
            raise MemoryWriteError(f"Mem0 write failed: {exc}") from exc

    async def is_stateless(self, agent_id: str) -> bool:
        # Mem0 adapter has no contract knowledge; always treat as stateful.
        # The AgentRunner enforces statelessness via AgentContract.
        return False


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _user_id(scope: MemoryScope) -> str:
    parts = [scope.org_id]
    if scope.department:
        parts.append(scope.department)
    if scope.session_id:
        parts.append(str(scope.session_id))
    return ":".join(parts)


def _scope_filters(scope: MemoryScope) -> dict[str, Any]:
    filters: dict[str, Any] = {"org_id": scope.org_id}
    if scope.department is not None:
        filters["dept_id"] = scope.department
    if scope.session_id is not None:
        filters["session_id"] = str(scope.session_id)
    if scope.agent_id is not None:
        filters["agent_id"] = scope.agent_id
    return filters


def _mem0_to_entry(raw: Any, scope: MemoryScope) -> MemoryEntry:
    meta: dict[str, Any] = raw.get("metadata") or {}
    return MemoryEntry(
        entry_id=uuid4(),
        org_id=scope.org_id,
        agent_id=meta.get("agent_id") or scope.agent_id or "unknown",
        scope=scope.department or "default",
        department=scope.department,
        session_id=scope.session_id,
        tier=meta.get("tier", "episodic"),
        content_text=raw.get("memory") or raw.get("content") or "",
        metadata={k: v for k, v in meta.items() if k not in {
            "org_id", "dept_id", "session_id", "agent_id", "tier"
        }},
        created_at=datetime.utcnow(),
        created_by_agent=meta.get("agent_id") or "mem0",
    )
