"""Memory adapter implementations.

skylize.memory.adapters.mem0_adapter — Mem0 cloud adapter (pure Python, no DB driver).

There is no Postgres memory adapter. `skylize.dal.memory_adapter` was deleted as
dead code: it targeted a table (`agent_memory_entries`) that no migration creates,
and nothing constructed it.

Mem0Adapter is itself constructed nowhere in src/ — this whole package is on the
orphan allowlist (scripts/orphan_modules.txt) awaiting a disposition.
"""
