"""MemoryAdapter Protocol — the boundary between the memory layer and its backends.

Concrete adapters must satisfy this Protocol.  They live in:
  - skylize.memory.adapters.mem0_adapter  (Mem0 cloud — secondary)
  - skylize.dal.memory_adapter            (Postgres — primary; asyncpg lives in dal/)

Import-linter note: this module is in skylize.memory and must remain driver-free.
It may import from skylize.schemas and skylize.errors only.
"""

from __future__ import annotations

from typing import Protocol

from ..schemas.memory import MemoryEntry, MemoryScope


class MemoryAdapter(Protocol):
    """Uniform interface for reading and writing agent memory entries.

    Both the Mem0 cloud adapter and the Postgres adapter satisfy this Protocol.
    """

    async def retrieve(self, scope: MemoryScope) -> list[MemoryEntry]: ...

    async def store(self, scope: MemoryScope, entry: MemoryEntry) -> None: ...

    async def is_stateless(self, agent_id: str) -> bool: ...
