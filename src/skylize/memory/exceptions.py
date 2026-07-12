"""Memory-layer exception hierarchy."""

from __future__ import annotations


class MemoryPermissionDenied(Exception):
    """Agent's contract forbids the requested memory read or write."""


class MemoryWriteError(Exception):
    """Unrecoverable failure persisting a memory entry."""


class MemoryNamespaceViolation(Exception):
    """scope.org_id does not match the caller's JWT org_id — cross-tenant leak attempt."""
