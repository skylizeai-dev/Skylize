"""Stripe Connect account repository: identity + authority, no bearer token.

Backs `ToolStripeProfile` (migration 0031, design 4.0.2). Every read and write
goes through ``Database.tenant_session(org_id)`` AND carries an explicit
``org_id`` predicate, so the RLS policy and the query agree rather than the
query relying on RLS alone - the same discipline as ``PgGcpWifRepository``
(dal/gcp_wif.py) and ``PgPermissionGrantRepository`` (dal/permission_grants.py:5-8).

NOTHING HERE IS A BEARER SECRET (design 4.0.1, Q2.1i). `stripe_account_id` is
an identifier ("acct_..."); possessing it authorizes nothing without the
platform's own secret key. There is therefore no encrypted column and no
`key_id`, unlike `PgOAuthCredentialRepository` and `PgCredentialRepository`.

ROWS ARE NEVER HARD-DELETED. Disconnecting an account sets `deauthorized_at`;
deleting the row would destroy the audit trail of an account that once held
authority (design 4.0.2), the same reasoning `PgGcpWifRepository.set_connection_state`
uses to never delete a broken federation row either (dal/gcp_wif.py:272-273).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class StripeAccountRow:
    id: UUID
    org_id: str
    stripe_account_id: str
    livemode: bool
    scope: str
    connected_at: datetime
    deauthorized_at: datetime | None


class StripeAccountRepository(Protocol):
    async def insert(self, row: StripeAccountRow) -> None: ...

    async def get_connected(
        self, org_id: str, *, livemode: bool
    ) -> StripeAccountRow | None: ...

    async def deauthorize(
        self, *, org_id: str, stripe_account_id: str, deauthorized_at: datetime
    ) -> bool: ...


_COLUMNS = (
    "id, org_id, stripe_account_id, livemode, scope, connected_at, deauthorized_at"
)


def _account_row(rec: Any) -> StripeAccountRow:
    return StripeAccountRow(
        id=rec["id"],
        org_id=rec["org_id"],
        stripe_account_id=rec["stripe_account_id"],
        livemode=rec["livemode"],
        scope=rec["scope"],
        connected_at=rec["connected_at"],
        deauthorized_at=rec["deauthorized_at"],
    )


class PgStripeAccountRepository:
    """Postgres-backed Stripe Connect account store. Every statement runs in a
    tenant session."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def insert(self, row: StripeAccountRow) -> None:
        async with self._db.tenant_session(row.org_id) as conn:
            await conn.execute(
                f"INSERT INTO org_stripe_accounts ({_COLUMNS}) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7)",
                row.id, row.org_id, row.stripe_account_id, row.livemode,
                row.scope, row.connected_at, row.deauthorized_at,
            )

    async def get_connected(
        self, org_id: str, *, livemode: bool
    ) -> StripeAccountRow | None:
        """The live (deauthorized_at IS NULL) row for this org and mode, or None.

        Deliberately NO fallback between modes in either direction (design
        4.0.2): a live process must never silently act through a test
        connection, and a test process must never reach live money.
        """
        async with self._db.tenant_session(org_id) as conn:
            r = await conn.fetchrow(
                f"SELECT {_COLUMNS} FROM org_stripe_accounts "
                "WHERE org_id=$1 AND livemode=$2 AND deauthorized_at IS NULL",
                org_id, livemode,
            )
            return None if r is None else _account_row(r)

    async def deauthorize(
        self, *, org_id: str, stripe_account_id: str, deauthorized_at: datetime
    ) -> bool:
        """Mark the account deauthorized. The row is NEVER deleted - see module
        docstring."""
        async with self._db.tenant_session(org_id) as conn:
            tag = await conn.execute(
                "UPDATE org_stripe_accounts SET deauthorized_at=$3 "
                "WHERE org_id=$1 AND stripe_account_id=$2 AND deauthorized_at IS NULL",
                org_id, stripe_account_id, deauthorized_at,
            )
            return bool(tag != "UPDATE 0")


class InMemoryStripeAccountRepository:
    """Test/memory-backend twin. Carries the same explicit org+livemode
    predicate the Pg implementation does, so a test that leaked across orgs or
    modes here would leak there too."""

    def __init__(self) -> None:
        self._rows: dict[UUID, StripeAccountRow] = {}

    async def insert(self, row: StripeAccountRow) -> None:
        self._rows[row.id] = row

    async def get_connected(
        self, org_id: str, *, livemode: bool
    ) -> StripeAccountRow | None:
        for row in self._rows.values():
            if (
                row.org_id == org_id
                and row.livemode == livemode
                and row.deauthorized_at is None
            ):
                return row
        return None

    async def deauthorize(
        self, *, org_id: str, stripe_account_id: str, deauthorized_at: datetime
    ) -> bool:
        for rid, row in self._rows.items():
            if (
                row.org_id == org_id
                and row.stripe_account_id == stripe_account_id
                and row.deauthorized_at is None
            ):
                self._rows[rid] = replace(row, deauthorized_at=deauthorized_at)
                return True
        return False
