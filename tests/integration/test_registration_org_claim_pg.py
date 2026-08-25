"""Registration claims an org atomically — proven on REAL Postgres.

`POST /api/v1/auth/register` is unauthenticated and is the only path that mints
an org owner, so "only a NEW org may be registered" is a security rule, not a
UX rule. It cannot be enforced by reading the org's user list and then writing:
under READ COMMITTED, two concurrent registrations for the same NEW org both see
an empty org, and a `WHERE NOT EXISTS` subquery takes no lock on rows that do not
exist yet, so both would insert an owner.

What actually settles it is the partial unique index from migration 0017
(`users_one_owner_per_org`). These cases drive the repository concurrently
against a real server and assert exactly one winner.

Skipped unless SKYLIZE_TEST_DB_URL is set.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio

from skylize.dal.connection import Database
from skylize.dal.ports import UserRow
from skylize.dal.users import PgUserRepository

from .conftest import DB_URL, requires_pg

pytestmark = pytest.mark.integration


def _org() -> str:
    return f"claim_{uuid.uuid4().hex[:8]}"


def _user(org: str, *, email: str, roles: list[str] | None = None) -> UserRow:
    return UserRow(
        user_id=uuid.uuid4(),
        org_id=org,
        email=email,
        password_hash="not-a-real-hash",
        display_name=None,
        roles=roles if roles is not None else ["owner"],
        is_active=True,
        created_at=datetime.now(timezone.utc),
        last_login_at=None,
    )


@pytest_asyncio.fixture()
async def db(migrated_public: None) -> AsyncIterator[Database]:
    if not DB_URL:
        pytest.skip("SKYLIZE_TEST_DB_URL not set")
    database = Database(DB_URL)
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


async def _seed_tenant(conn: Any, org: str) -> None:
    # users.org_id is FK -> tenants.org_id (migration 0008).
    await conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _cleanup(conn: Any, org: str) -> None:
    for sql in (
        "DELETE FROM users WHERE org_id=$1",
        "DELETE FROM tenants WHERE org_id=$1",
    ):
        try:
            await conn.execute(sql, org)
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass


@requires_pg
async def test_simultaneous_registrations_produce_exactly_one_owner(
    db, admin_conn
) -> None:
    """The race the read-then-write could not win.

    Eight coroutines claim the same brand-new org at once. Exactly one may be
    written, and it must be an owner — two owners in one org is the outcome the
    unique index exists to make impossible.
    """
    org = _org()
    repo = PgUserRepository(db)
    try:
        await _seed_tenant(admin_conn, org)

        results = await asyncio.gather(
            *(
                repo.create_owner_of_new_org(_user(org, email=f"c{n}@example.com"))
                for n in range(8)
            )
        )

        assert sum(1 for r in results if r) == 1, results
        rows = await repo.list_by_org(org)
        assert len(rows) == 1
        assert rows[0].roles == ["owner"]
    finally:
        await _cleanup(admin_conn, org)


@requires_pg
async def test_registering_into_an_occupied_org_is_refused(db, admin_conn) -> None:
    org = _org()
    repo = PgUserRepository(db)
    try:
        await _seed_tenant(admin_conn, org)
        assert await repo.create_owner_of_new_org(_user(org, email="first@example.com"))

        claimed = await repo.create_owner_of_new_org(_user(org, email="second@example.com"))

        assert claimed is False
        assert len(await repo.list_by_org(org)) == 1
    finally:
        await _cleanup(admin_conn, org)


@requires_pg
async def test_an_org_holding_only_a_non_owner_is_still_refused(db, admin_conn) -> None:
    """The rule is 'the org has NO users', not 'the org has no owner'.

    The unique index alone would let this through — it constrains owners only.
    The conditional insert is what covers it, so both mechanisms are load-bearing.
    """
    org = _org()
    repo = PgUserRepository(db)
    try:
        await _seed_tenant(admin_conn, org)
        # A viewer written directly through the repository, as an invite flow
        # would: no owner exists in this org.
        await repo.create_user(_user(org, email="viewer@example.com", roles=["viewer"]))

        claimed = await repo.create_owner_of_new_org(_user(org, email="late@example.com"))

        assert claimed is False
        assert len(await repo.list_by_org(org)) == 1
    finally:
        await _cleanup(admin_conn, org)


@requires_pg
async def test_a_duplicate_email_still_raises_rather_than_reporting_org_taken(
    db, admin_conn
) -> None:
    """`users_email_unique` is a DIFFERENT condition and must not be swallowed as
    'org not available' — the caller needs to be told to log in, not to pick
    another org."""
    import asyncpg

    org_a, org_b = _org(), _org()
    repo = PgUserRepository(db)
    try:
        await _seed_tenant(admin_conn, org_a)
        await _seed_tenant(admin_conn, org_b)
        assert await repo.create_owner_of_new_org(_user(org_a, email="dup@example.com"))

        with pytest.raises(asyncpg.UniqueViolationError) as ei:
            await repo.create_owner_of_new_org(_user(org_b, email="dup@example.com"))
        assert ei.value.constraint_name == "users_email_unique"
    finally:
        await _cleanup(admin_conn, org_a)
        await _cleanup(admin_conn, org_b)


@requires_pg
async def test_the_index_exists_and_is_partial_on_the_owner_role(admin_conn) -> None:
    """Pin the constraint itself: it is the whole race guarantee, and a future
    migration that widened or dropped it would silently restore the defect."""
    definition = await admin_conn.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'users_one_owner_per_org'"
    )
    assert definition is not None, "migration 0017's unique index is missing"
    assert "UNIQUE" in definition
    assert "org_id" in definition
    assert "owner" in definition  # partial: owners only, so an invite flow stays possible
