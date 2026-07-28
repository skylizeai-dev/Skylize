"""
The database connection pool and tenant-scoped session.

This is the ONLY module that opens an asyncpg connection. Every tenant query is
issued inside `tenant_session(org_id)`, which runs `SET LOCAL skylize.org_id`
so the RLS policies (migration 0001) filter to that tenant — isolation holds at
the data layer regardless of upstream checks.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg


def _encode_json(value: Any) -> str:
    """Encoder for the json/jsonb codec.

    Every DAL write site passes JSONB parameters as pre-serialized ``str``
    (``json.dumps(...)`` at the call site), so a ``str`` must pass through
    untouched — running it through ``json.dumps`` again would store a JSON
    string scalar instead of the document. Non-str values (dicts/lists) are
    serialized here, so passing a raw object also works.
    """
    return value if isinstance(value, str) else json.dumps(value)


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Register json/jsonb codecs so JSONB decodes to Python objects uniformly.

    Without this, asyncpg returns JSONB columns as ``str`` and every read site
    must decode (or crash) on its own — the defect class behind the
    deliverables 500. Runs once per pooled connection via ``create_pool(init=)``.
    """
    for type_name in ("json", "jsonb"):
        await conn.set_type_codec(
            type_name,
            encoder=_encode_json,
            decoder=json.loads,
            schema="pg_catalog",
        )


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=10, init=_init_connection
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database not connected; call connect() first")
        return self._pool

    @asynccontextmanager
    async def tenant_session(self, org_id: str) -> AsyncIterator[asyncpg.Connection]:
        """Acquire a connection bound to `org_id` for the duration of a transaction.

        `SET LOCAL` scopes the org_id to this transaction, so RLS applies and the
        setting is discarded on commit/rollback — no leakage across pooled reuse.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('skylize.org_id', $1, true)", org_id)
                yield conn

    @asynccontextmanager
    async def admin_session(self) -> AsyncIterator[asyncpg.Connection]:
        """Connection without a tenant binding — for platform-level tables only
        (tenants, agent_contracts). RLS tables return nothing here by design."""
        async with self.pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def rehydration_session(self) -> AsyncIterator[asyncpg.Connection]:
        """Read-only platform-wide session for snapshot rehydration at startup.

        Sets `skylize.rehydrate = 'on'`, which the RLS `tenant_isolation` policy
        treats as a read carve-out (migration 0002) so the Governance Authority
        can warm its kill/revocation/suspension snapshot across ALL tenants.
        Used only at startup, never on a request path; writes are not permitted
        (the policy's WITH CHECK still requires a matching org_id).
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('skylize.rehydrate', 'on', true)")
                yield conn
