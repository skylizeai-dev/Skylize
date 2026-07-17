"""MemoryGateway — contract-level permission enforcement for memory access.

Sits between LLMAgentRunner and the MemoryAdapter.  Every read/write passes
through this gateway, which:
  1. Resolves the agent's contract from the registry and checks memory_read_access /
     memory_write_access (empty list = stateless = denied).
  2. Guards the org_id namespace — scope.org_id MUST match the caller's JWT org_id
     (the most critical single check in the memory system).
  3. Matches the requested namespace (scope.department) against the contract's
     granted patterns — a non-empty grant list is not enough on its own; the
     requested namespace must actually be covered by one of the patterns.
  4. Skips writes where importance_score < 0.40 (low-signal noise filter).
  5. Emits structured audit log entries for every denied, skipped, or violated access.
"""

from __future__ import annotations

import structlog

from ..contracts.registry import AgentRegistry
from ..schemas.memory import MemoryEntry, MemoryScope
from .exceptions import MemoryNamespaceViolation, MemoryPermissionDenied
from .ports import MemoryAdapter

log = structlog.get_logger(__name__)


def _namespace_granted(requested: str, granted: list[str]) -> bool:
    """Check whether `requested` is covered by one of the `granted` patterns.

    A pattern ending in ``*`` is a prefix match on everything before the ``*``;
    any other pattern must match exactly. Patterns are matched as declared in
    the contract — no normalization of near-duplicates (e.g. "security:fraud:*"
    and "security:patterns" both stay literal).
    """
    for pattern in granted:
        if pattern.endswith("*"):
            if requested.startswith(pattern[:-1]):
                return True
        elif requested == pattern:
            return True
    return False


class MemoryGateway:
    def __init__(self, *, adapter: MemoryAdapter, registry: AgentRegistry) -> None:
        self._adapter = adapter
        self._registry = registry

    async def read(
        self,
        agent_id: str,
        scope: MemoryScope,
        *,
        caller_org_id: str,
    ) -> list[MemoryEntry]:
        # Namespace guard — must come before any data access.
        if scope.org_id != caller_org_id:
            log.critical(
                "memory.namespace_violation",
                agent_id=agent_id,
                scope_org_id=scope.org_id,
                caller_org_id=caller_org_id,
            )
            raise MemoryNamespaceViolation(
                f"scope.org_id={scope.org_id!r} does not match caller org_id={caller_org_id!r}"
            )

        contract = self._registry.resolve(agent_id)
        if not contract.memory_read_access:
            log.warning(
                "memory.read_denied",
                agent_id=agent_id,
                scope_org_id=scope.org_id,
            )
            raise MemoryPermissionDenied(f"{agent_id} has no memory_read_access")

        if scope.department is not None and not _namespace_granted(
            scope.department, contract.memory_read_access
        ):
            log.warning(
                "memory.read_denied_namespace_mismatch",
                agent_id=agent_id,
                scope_org_id=scope.org_id,
                requested_namespace=scope.department,
                granted=contract.memory_read_access,
            )
            raise MemoryPermissionDenied(
                f"{agent_id} is not granted read access to namespace {scope.department!r}"
            )

        return await self._adapter.retrieve(scope)

    async def write(
        self,
        agent_id: str,
        scope: MemoryScope,
        entry: MemoryEntry,
        *,
        caller_org_id: str,
    ) -> None:
        # Namespace guard.
        if scope.org_id != caller_org_id:
            log.critical(
                "memory.namespace_violation",
                agent_id=agent_id,
                scope_org_id=scope.org_id,
                caller_org_id=caller_org_id,
            )
            raise MemoryNamespaceViolation(
                f"scope.org_id={scope.org_id!r} does not match caller org_id={caller_org_id!r}"
            )

        contract = self._registry.resolve(agent_id)
        if not contract.memory_write_access:
            log.warning(
                "memory.write_denied",
                agent_id=agent_id,
                scope_org_id=scope.org_id,
            )
            raise MemoryPermissionDenied(f"{agent_id} has no memory_write_access")

        if scope.department is not None and not _namespace_granted(
            scope.department, contract.memory_write_access
        ):
            log.warning(
                "memory.write_denied_namespace_mismatch",
                agent_id=agent_id,
                scope_org_id=scope.org_id,
                requested_namespace=scope.department,
                granted=contract.memory_write_access,
            )
            raise MemoryPermissionDenied(
                f"{agent_id} is not granted write access to namespace {scope.department!r}"
            )

        if entry.importance_score < 0.40:
            log.info(
                "memory.write_skipped_low_score",
                agent_id=agent_id,
                importance_score=entry.importance_score,
            )
            return

        await self._adapter.store(scope, entry)
