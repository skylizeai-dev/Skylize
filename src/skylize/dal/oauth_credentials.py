"""
OAuth grant repository: OAuthCredentialRow, Protocol, asyncpg and in-memory impls.

The oauth_credentials table is RLS-scoped (migration 0021), so every read and
write goes through ``Database.tenant_session(org_id)``. Each statement ALSO
carries an explicit ``org_id`` predicate, so the policy and the query agree
rather than the query relying on RLS alone — the same belt-and-braces discipline
as ``PgCredentialRepository`` (dal/credentials.py:80-123).

Nothing here decrypts. The repository moves ciphertext; `app/credentials/oauth.py`
owns the key.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

#: Durable connection state. See migration 0021's module docstring for why
#: 'expired' is not redundant with `expires_at`: a grant whose access token is
#: past expiry but which holds a refresh token is still 'valid', because refresh
#: repairs it without a human.
ConnectionState = Literal["valid", "expired", "revoked"]


@dataclass(frozen=True, slots=True)
class OAuthCredentialRow:
    cred_id: UUID
    org_id: str
    provider: str
    label: str                          # '' = the default connection for this provider
    provider_account_id: str            # which upstream account this grant names
    key_id: str                         # which encryption key produced the ciphertext
    encrypted_access_token: str
    encrypted_refresh_token: str | None  # None when the provider issues no refresh token
    #: NULL = this grant does not expire by time (migration 0023). NOT "unknown"
    #: and NOT "expired": such a grant is never stale by clock, and its liveness
    #: is carried entirely by `connection_state`.
    expires_at: datetime | None
    scopes: tuple[str, ...]
    connection_state: ConnectionState
    state_reason: str | None
    created_at: datetime
    updated_at: datetime
    refreshed_at: datetime | None


class OAuthCredentialRepository(Protocol):
    async def insert(self, row: OAuthCredentialRow) -> None: ...

    async def get(
        self, org_id: str, provider: str, label: str
    ) -> OAuthCredentialRow | None: ...

    async def get_for_update(
        self, conn: Any, org_id: str, provider: str, label: str
    ) -> OAuthCredentialRow | None: ...

    async def update_tokens(
        self,
        *,
        conn: Any,
        cred_id: UUID,
        org_id: str,
        encrypted_access_token: str,
        encrypted_refresh_token: str | None,
        expires_at: datetime | None,
        key_id: str,
        refreshed_at: datetime,
    ) -> bool: ...

    async def set_connection_state(
        self,
        *,
        cred_id: UUID,
        org_id: str,
        state: ConnectionState,
        reason: str | None,
        conn: Any = None,
    ) -> bool: ...

    async def list_for_org(self, org_id: str) -> list[OAuthCredentialRow]: ...


# ---------------------------------------------------------------------------
# asyncpg implementation
# ---------------------------------------------------------------------------

_COLUMNS = (
    "cred_id, org_id, provider, label, provider_account_id, key_id, "
    "encrypted_access_token, encrypted_refresh_token, expires_at, scopes, "
    "connection_state, state_reason, created_at, updated_at, refreshed_at"
)


def _oauth_row(rec: Any) -> OAuthCredentialRow:
    return OAuthCredentialRow(
        cred_id=rec["cred_id"],
        org_id=rec["org_id"],
        provider=rec["provider"],
        label=rec["label"],
        provider_account_id=rec["provider_account_id"],
        key_id=rec["key_id"],
        encrypted_access_token=rec["encrypted_access_token"],
        encrypted_refresh_token=rec["encrypted_refresh_token"],
        expires_at=rec["expires_at"],
        scopes=tuple(rec["scopes"] or ()),
        connection_state=rec["connection_state"],
        state_reason=rec["state_reason"],
        created_at=rec["created_at"],
        updated_at=rec["updated_at"],
        refreshed_at=rec["refreshed_at"],
    )


class PgOAuthCredentialRepository:
    """Postgres-backed grant store. Every statement runs inside a tenant session.

    The ``conn``-taking methods exist so a read-modify-write refresh can hold ONE
    transaction across the row lock and the write. Passing the connection is the
    only way to do that here: ``tenant_session`` opens a transaction per call, so
    a second call would be a second transaction and the ``FOR UPDATE`` lock taken
    in the first would already have been released.
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    def tenant_session(self, org_id: str) -> Any:
        """Expose the caller's transaction scope so the refresh path can hold a
        single transaction across lock -> refresh -> write."""
        return self._db.tenant_session(org_id)

    async def insert(self, row: OAuthCredentialRow) -> None:
        async with self._db.tenant_session(row.org_id) as conn:
            await conn.execute(
                f"""
                INSERT INTO oauth_credentials ({_COLUMNS})
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                """,
                row.cred_id, row.org_id, row.provider, row.label,
                row.provider_account_id, row.key_id,
                row.encrypted_access_token, row.encrypted_refresh_token,
                row.expires_at, list(row.scopes),
                row.connection_state, row.state_reason,
                row.created_at, row.updated_at, row.refreshed_at,
            )

    async def get(
        self, org_id: str, provider: str, label: str
    ) -> OAuthCredentialRow | None:
        async with self._db.tenant_session(org_id) as conn:
            r = await conn.fetchrow(
                f"SELECT {_COLUMNS} FROM oauth_credentials "
                "WHERE org_id=$1 AND provider=$2 AND label=$3",
                org_id, provider, label,
            )
            return None if r is None else _oauth_row(r)

    async def get_for_update(
        self, conn: Any, org_id: str, provider: str, label: str
    ) -> OAuthCredentialRow | None:
        """Row-locked read inside the CALLER's transaction.

        `FOR UPDATE` serialises concurrent refreshes of the same grant. Two agent
        runs for one org can reach the refresh path simultaneously, and a provider
        that rotates refresh tokens would let the loser persist a token the
        provider has already invalidated. The lock makes the second waiter re-read
        the winner's result and find it fresh.
        """
        r = await conn.fetchrow(
            f"SELECT {_COLUMNS} FROM oauth_credentials "
            "WHERE org_id=$1 AND provider=$2 AND label=$3 FOR UPDATE",
            org_id, provider, label,
        )
        return None if r is None else _oauth_row(r)

    async def update_tokens(
        self,
        *,
        conn: Any,
        cred_id: UUID,
        org_id: str,
        encrypted_access_token: str,
        encrypted_refresh_token: str | None,
        expires_at: datetime | None,
        key_id: str,
        refreshed_at: datetime,
    ) -> bool:
        """Persist a successful refresh. Clears any prior non-valid state: a
        refresh that succeeded is proof the grant is live again."""
        tag = await conn.execute(
            """
            UPDATE oauth_credentials
               SET encrypted_access_token=$3,
                   encrypted_refresh_token=$4,
                   expires_at=$5,
                   key_id=$6,
                   refreshed_at=$7,
                   updated_at=$7,
                   connection_state='valid',
                   state_reason=NULL
             WHERE cred_id=$1 AND org_id=$2
            """,
            cred_id, org_id, encrypted_access_token, encrypted_refresh_token,
            expires_at, key_id, refreshed_at,
        )
        return bool(tag != "UPDATE 0")

    async def set_connection_state(
        self,
        *,
        cred_id: UUID,
        org_id: str,
        state: ConnectionState,
        reason: str | None,
        conn: Any = None,
    ) -> bool:
        """Mark the grant's durable state. The row is NEVER deleted: deletion
        would destroy the evidence that a connection existed and was lost."""
        sql = (
            "UPDATE oauth_credentials "
            "SET connection_state=$3, state_reason=$4, updated_at=now() "
            "WHERE cred_id=$1 AND org_id=$2"
        )
        if conn is not None:
            tag = await conn.execute(sql, cred_id, org_id, state, reason)
            return bool(tag != "UPDATE 0")
        async with self._db.tenant_session(org_id) as own_conn:
            tag = await own_conn.execute(sql, cred_id, org_id, state, reason)
            return bool(tag != "UPDATE 0")

    async def list_for_org(self, org_id: str) -> list[OAuthCredentialRow]:
        async with self._db.tenant_session(org_id) as conn:
            rows = await conn.fetch(
                f"SELECT {_COLUMNS} FROM oauth_credentials "
                "WHERE org_id=$1 ORDER BY provider, label",
                org_id,
            )
            return [_oauth_row(r) for r in rows]


# ---------------------------------------------------------------------------
# In-memory implementation (tests / memory backend)
# ---------------------------------------------------------------------------

class _NullTxn:
    """Stands in for the asyncpg transaction scope on the memory backend."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


class InMemoryOAuthCredentialRepository:
    """Test/memory-backend twin. Tenancy is enforced by the same explicit
    ``org_id`` predicate the Pg implementation carries, so a test that leaks
    across orgs here would leak there too."""

    def __init__(self) -> None:
        self._store: dict[UUID, OAuthCredentialRow] = {}

    def tenant_session(self, org_id: str) -> Any:  # noqa: ARG002 — parity only
        return _NullTxn()

    async def insert(self, row: OAuthCredentialRow) -> None:
        self._store[row.cred_id] = row

    async def get(
        self, org_id: str, provider: str, label: str
    ) -> OAuthCredentialRow | None:
        for row in self._store.values():
            if row.org_id == org_id and row.provider == provider and row.label == label:
                return row
        return None

    async def get_for_update(
        self, conn: Any, org_id: str, provider: str, label: str  # noqa: ARG002
    ) -> OAuthCredentialRow | None:
        return await self.get(org_id, provider, label)

    async def update_tokens(
        self,
        *,
        conn: Any = None,  # noqa: ARG002
        cred_id: UUID,
        org_id: str,
        encrypted_access_token: str,
        encrypted_refresh_token: str | None,
        expires_at: datetime | None,
        key_id: str,
        refreshed_at: datetime,
    ) -> bool:
        row = self._store.get(cred_id)
        if row is None or row.org_id != org_id:
            return False
        self._store[cred_id] = replace(
            row,
            encrypted_access_token=encrypted_access_token,
            encrypted_refresh_token=encrypted_refresh_token,
            expires_at=expires_at,
            key_id=key_id,
            refreshed_at=refreshed_at,
            updated_at=refreshed_at,
            connection_state="valid",
            state_reason=None,
        )
        return True

    async def set_connection_state(
        self,
        *,
        cred_id: UUID,
        org_id: str,
        state: ConnectionState,
        reason: str | None,
        conn: Any = None,  # noqa: ARG002
    ) -> bool:
        row = self._store.get(cred_id)
        if row is None or row.org_id != org_id:
            return False
        self._store[cred_id] = replace(row, connection_state=state, state_reason=reason)
        return True

    async def list_for_org(self, org_id: str) -> list[OAuthCredentialRow]:
        return sorted(
            (r for r in self._store.values() if r.org_id == org_id),
            key=lambda r: (r.provider, r.label),
        )
