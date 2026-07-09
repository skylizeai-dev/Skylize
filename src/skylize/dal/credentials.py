"""
Credential repository: CredentialRow, Protocol, asyncpg and in-memory implementations.

The org_credentials table is RLS-scoped (migration 0010), so all reads/writes
go through Database.tenant_session(org_id).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CredentialRow:
    cred_id: UUID
    org_id: str
    provider: str
    label: str        # '' = default credential for this provider
    encrypted_value: str
    metadata_json: str  # JSON-encoded dict
    created_at: datetime
    rotated_at: datetime | None


class CredentialRepository(Protocol):
    async def insert(self, row: CredentialRow) -> None: ...

    async def get(self, org_id: str, provider: str, label: str) -> CredentialRow | None: ...

    async def get_by_id(self, cred_id: UUID, org_id: str) -> CredentialRow | None: ...

    async def update_encrypted_value(
        self, cred_id: UUID, org_id: str, encrypted_value: str, rotated_at: datetime
    ) -> bool: ...

    async def delete_by_id(self, cred_id: UUID, org_id: str) -> bool: ...

    async def list_providers(self, org_id: str) -> list[str]: ...


# ---------------------------------------------------------------------------
# asyncpg implementation
# ---------------------------------------------------------------------------

def _cred_row(rec: Any) -> CredentialRow:
    meta = rec["metadata_json"]
    if isinstance(meta, (dict, list)):
        meta = json.dumps(meta)
    return CredentialRow(
        cred_id=rec["id"],
        org_id=rec["org_id"],
        provider=rec["provider"],
        label=rec["label"],
        encrypted_value=rec["encrypted_value"],
        metadata_json=meta or "{}",
        created_at=rec["created_at"],
        rotated_at=rec["rotated_at"],
    )


class PgCredentialRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def insert(self, row: CredentialRow) -> None:
        async with self._db.tenant_session(row.org_id) as conn:
            await conn.execute(
                """
                INSERT INTO org_credentials
                    (id, org_id, provider, label, encrypted_value, metadata_json, created_at)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                """,
                row.cred_id, row.org_id, row.provider, row.label,
                row.encrypted_value, row.metadata_json, row.created_at,
            )

    async def get(self, org_id: str, provider: str, label: str) -> CredentialRow | None:
        async with self._db.tenant_session(org_id) as conn:
            r = await conn.fetchrow(
                "SELECT * FROM org_credentials "
                "WHERE org_id=$1 AND provider=$2 AND label=$3",
                org_id, provider, label,
            )
            return None if r is None else _cred_row(r)

    async def get_by_id(self, cred_id: UUID, org_id: str) -> CredentialRow | None:
        async with self._db.tenant_session(org_id) as conn:
            r = await conn.fetchrow(
                "SELECT * FROM org_credentials WHERE id=$1 AND org_id=$2",
                cred_id, org_id,
            )
            return None if r is None else _cred_row(r)

    async def update_encrypted_value(
        self, cred_id: UUID, org_id: str, encrypted_value: str, rotated_at: datetime
    ) -> bool:
        async with self._db.tenant_session(org_id) as conn:
            tag = await conn.execute(
                "UPDATE org_credentials SET encrypted_value=$3, rotated_at=$4 "
                "WHERE id=$1 AND org_id=$2",
                cred_id, org_id, encrypted_value, rotated_at,
            )
            return bool(tag != "UPDATE 0")

    async def delete_by_id(self, cred_id: UUID, org_id: str) -> bool:
        async with self._db.tenant_session(org_id) as conn:
            tag = await conn.execute(
                "DELETE FROM org_credentials WHERE id=$1 AND org_id=$2",
                cred_id, org_id,
            )
            return bool(tag != "DELETE 0")

    async def list_providers(self, org_id: str) -> list[str]:
        async with self._db.tenant_session(org_id) as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT provider FROM org_credentials "
                "WHERE org_id=$1 ORDER BY provider",
                org_id,
            )
            return [r["provider"] for r in rows]


# ---------------------------------------------------------------------------
# In-memory implementation (tests / memory backend)
# ---------------------------------------------------------------------------

class InMemoryCredentialRepository:
    def __init__(self) -> None:
        self._store: dict[UUID, CredentialRow] = {}

    async def insert(self, row: CredentialRow) -> None:
        self._store[row.cred_id] = row

    async def get(self, org_id: str, provider: str, label: str) -> CredentialRow | None:
        for row in self._store.values():
            if row.org_id == org_id and row.provider == provider and row.label == label:
                return row
        return None

    async def get_by_id(self, cred_id: UUID, org_id: str) -> CredentialRow | None:
        row = self._store.get(cred_id)
        return row if row is not None and row.org_id == org_id else None

    async def update_encrypted_value(
        self, cred_id: UUID, org_id: str, encrypted_value: str, rotated_at: datetime
    ) -> bool:
        row = self._store.get(cred_id)
        if row is None or row.org_id != org_id:
            return False
        self._store[cred_id] = replace(row, encrypted_value=encrypted_value, rotated_at=rotated_at)
        return True

    async def delete_by_id(self, cred_id: UUID, org_id: str) -> bool:
        row = self._store.get(cred_id)
        if row is None or row.org_id != org_id:
            return False
        del self._store[cred_id]
        return True

    async def list_providers(self, org_id: str) -> list[str]:
        return sorted({row.provider for row in self._store.values() if row.org_id == org_id})
