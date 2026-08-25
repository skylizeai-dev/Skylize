"""MemoryAdapter Protocol — the boundary between the memory layer and its backends.

Concrete adapters must satisfy this Protocol. Exactly one exists today:
  - skylize.memory.adapters.mem0_adapter  (Mem0 cloud)

NO POSTGRES ADAPTER EXISTS. `skylize.dal.memory_adapter` (PgMemoryAdapter) was
deleted: it read and wrote a table, `agent_memory_entries`, that no migration
ever created, and nothing in src/ or tests/ constructed it. Keeping it made the
repo appear to have durable agent memory that it does not have. If agent-memory
persistence is built, it gets a schema designed then — not a table added to
justify code no caller reaches.

The one live consumer of this Protocol is MemoryGateway (permission enforcement),
which is itself unwired from bootstrap.

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
