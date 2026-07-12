"""Application-level error types.

Centralised here so every layer can import without creating circular deps.
"""

from __future__ import annotations


class MemoryRetrievalError(Exception):
    """Raised when the memory service fails to retrieve entries."""


class MemoryWriteError(Exception):
    """Raised when the memory service fails to persist an entry."""
