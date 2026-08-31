"""
Permission-grant repository: the org-level pre-authorization allow-list.

Backs `ToolPermissionProfile` (migration 0022). Every read and write goes through
``Database.tenant_session(org_id)`` AND carries an explicit ``org_id`` predicate,
so the RLS policy and the query agree rather than the query relying on RLS alone
- the same discipline as `PgCredentialRepository` (dal/credentials.py:80-123) and
`PgOAuthCredentialRepository` (dal/oauth_credentials.py).

An org with no rows can perform no elevated action. `list_for_action` returning
an empty list is the DENY case, and the gate treats it as such - see
app/permissions/gate.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

#: Ordered least- to most-privileged. `owner` is deliberately absent: transferring
#: ownership of a customer's file is out of scope for integration_inputs.md 2.5
#: and must not be expressible through this table.
GranteeRole = Literal["reader", "commenter", "writer"]

ROLE_RANK: dict[str, int] = {"reader": 0, "commenter": 1, "writer": 2}


@dataclass(frozen=True, slots=True)
class PermissionGrantRow:
    grant_id: UUID
    org_id: str
    action_class: str          # e.g. 'drive.permissions.create'
    grantee_pattern: str       # exact address or bare domain; never a regex/glob
    max_role: GranteeRole
    allow_link_sharing: bool
    created_at: datetime
    updated_at: datetime


class PermissionGrantRepository(Protocol):
    async def insert(self, row: PermissionGrantRow) -> None: ...

    async def list_for_action(
        self, org_id: str, action_class: str
    ) -> list[PermissionGrantRow]: ...

    async def delete_by_id(self, grant_id: UUID, org_id: str) -> bool: ...


_COLUMNS = (
    "grant_id, org_id, action_class, grantee_pattern, max_role, "
    "allow_link_sharing, created_at, updated_at"
)


def _grant_row(rec: Any) -> PermissionGrantRow:
    return PermissionGrantRow(
        grant_id=rec["grant_id"],
        org_id=rec["org_id"],
        action_class=rec["action_class"],
        grantee_pattern=rec["grantee_pattern"],
        max_role=rec["max_role"],
        allow_link_sharing=rec["allow_link_sharing"],
        created_at=rec["created_at"],
        updated_at=rec["updated_at"],
    )


class PgPermissionGrantRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def insert(self, row: PermissionGrantRow) -> None:
        async with self._db.tenant_session(row.org_id) as conn:
            await conn.execute(
                f"INSERT INTO org_permission_grants ({_COLUMNS}) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                row.grant_id, row.org_id, row.action_class, row.grantee_pattern,
                row.max_role, row.allow_link_sharing, row.created_at, row.updated_at,
            )

    async def list_for_action(
        self, org_id: str, action_class: str
    ) -> list[PermissionGrantRow]:
        async with self._db.tenant_session(org_id) as conn:
            rows = await conn.fetch(
                f"SELECT {_COLUMNS} FROM org_permission_grants "
                "WHERE org_id=$1 AND action_class=$2 ORDER BY grantee_pattern",
                org_id, action_class,
            )
            return [_grant_row(r) for r in rows]

    async def delete_by_id(self, grant_id: UUID, org_id: str) -> bool:
        async with self._db.tenant_session(org_id) as conn:
            tag = await conn.execute(
                "DELETE FROM org_permission_grants WHERE grant_id=$1 AND org_id=$2",
                grant_id, org_id,
            )
            return bool(tag != "DELETE 0")


class InMemoryPermissionGrantRepository:
    """Test/memory-backend twin. Carries the same explicit org predicate the Pg
    implementation does, so a test that leaked across orgs here would leak there."""

    def __init__(self) -> None:
        self._store: dict[UUID, PermissionGrantRow] = {}

    async def insert(self, row: PermissionGrantRow) -> None:
        self._store[row.grant_id] = row

    async def list_for_action(
        self, org_id: str, action_class: str
    ) -> list[PermissionGrantRow]:
        return sorted(
            (
                r for r in self._store.values()
                if r.org_id == org_id and r.action_class == action_class
            ),
            key=lambda r: r.grantee_pattern,
        )

    async def delete_by_id(self, grant_id: UUID, org_id: str) -> bool:
        row = self._store.get(grant_id)
        if row is None or row.org_id != org_id:
            return False
        del self._store[grant_id]
        return True
