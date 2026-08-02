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
  3b. For a `principal:{id}:*` namespace, binds that {id} to the CALLER's own
     principal. A contract grant is static and shared by every employee using
     the agent, so `principal:*` alone would let one employee's session read
     another's; the concrete principal must be checked at call time.
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


#: Namespaces under this prefix belong to ONE human principal and are readable
#: and writable only by that principal's own agents.
PRINCIPAL_NAMESPACE_PREFIX = "principal:"


def _principal_of(namespace: str) -> str | None:
    """The principal_id owning `namespace`, or None if it is not principal-scoped.

    Shape is ``principal:{principal_id}:{rest}``. A bare ``principal:`` or an
    empty id segment returns None, which callers treat as unownable and refuse —
    a namespace that claims to be principal-scoped but names no principal must
    never match anybody.
    """
    if not namespace.startswith(PRINCIPAL_NAMESPACE_PREFIX):
        return None
    remainder = namespace[len(PRINCIPAL_NAMESPACE_PREFIX):]
    principal_id, _, _ = remainder.partition(":")
    return principal_id or None


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

    def _assert_principal_binding(
        self,
        *,
        agent_id: str,
        scope: MemoryScope,
        caller_principal_id: str | None,
        access: str,
    ) -> None:
        """Bind a `principal:` namespace to the CALLER's own principal.

        WHY THE CONTRACT GRANT IS NOT ENOUGH. `memory_read_access` is static and
        shared by every employee using a given agent, so a contract granting
        `principal:*` would let Devon's co-work session read
        `principal:alice:notes` — the pattern matches, and nothing else in this
        gateway knows who is calling. The concrete principal has to be bound at
        CALL time, which is what this does. It is the same shape as the
        `caller_org_id` guard above, one level further in.

        Fail-closed in both unownable directions: a caller with no principal
        (an autonomous agent) can reach no principal namespace at all, and a
        namespace naming no principal is refused rather than matched.
        """
        if scope.department is None:
            return
        owner = _principal_of(scope.department)
        if owner is None:
            if scope.department.startswith(PRINCIPAL_NAMESPACE_PREFIX):
                log.critical(
                    "memory.principal_namespace_malformed",
                    agent_id=agent_id,
                    requested_namespace=scope.department,
                    access=access,
                )
                raise MemoryNamespaceViolation(
                    f"namespace {scope.department!r} is principal-scoped but names "
                    f"no principal; refusing rather than matching everybody"
                )
            return  # not a principal namespace — the contract check governs it

        if caller_principal_id is None:
            log.critical(
                "memory.principal_namespace_violation",
                agent_id=agent_id,
                requested_namespace=scope.department,
                caller_principal_id=None,
                access=access,
            )
            raise MemoryNamespaceViolation(
                f"{agent_id} requested principal namespace {scope.department!r} "
                f"with no principal bound to the call; a token that carries no "
                f"on_behalf_of claim can reach no principal's memory"
            )

        if owner != caller_principal_id:
            log.critical(
                "memory.principal_namespace_violation",
                agent_id=agent_id,
                requested_namespace=scope.department,
                namespace_owner=owner,
                caller_principal_id=caller_principal_id,
                access=access,
            )
            raise MemoryNamespaceViolation(
                f"{agent_id} may not {access} principal {owner!r}'s memory on "
                f"behalf of principal {caller_principal_id!r}"
            )

    async def read(
        self,
        agent_id: str,
        scope: MemoryScope,
        *,
        caller_org_id: str,
        caller_principal_id: str | None = None,
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

        # The contract said the SHAPE is allowed; this says the INSTANCE is the
        # caller's own. Both are required.
        self._assert_principal_binding(
            agent_id=agent_id,
            scope=scope,
            caller_principal_id=caller_principal_id,
            access="read",
        )

        return await self._adapter.retrieve(scope)

    async def write(
        self,
        agent_id: str,
        scope: MemoryScope,
        entry: MemoryEntry,
        *,
        caller_org_id: str,
        caller_principal_id: str | None = None,
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

        # Bind the concrete principal before anything is stored — writing INTO
        # another employee's namespace is as bad as reading out of it.
        self._assert_principal_binding(
            agent_id=agent_id,
            scope=scope,
            caller_principal_id=caller_principal_id,
            access="write",
        )

        if entry.importance_score < 0.40:
            log.info(
                "memory.write_skipped_low_score",
                agent_id=agent_id,
                importance_score=entry.importance_score,
            )
            return

        await self._adapter.store(scope, entry)
