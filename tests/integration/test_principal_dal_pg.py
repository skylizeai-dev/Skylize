"""PgPrincipalRepository against REAL Postgres, as the RLS-subject app role.

Covers what only a database can prove for migration 0019's `principal` /
`principal_grant`:

  * round-trip: a seeded principal and its grants come back as the pure kernel's
    models, with the columns the models do NOT declare (created_at, grant_id,
    created_by) correctly dropped rather than exploding against extra="forbid";
  * RLS: one org cannot read another org's principals or grants, proven as a
    role that is neither superuser nor the table owner -- the same shape, and
    the same role-attribute assertions, as test_work_journal_pg.py;
  * the cast in dal/principal.py:_principal is honest -- the column's CHECK
    constraint and the AuthorityLevel Literal carry the same five values;
  * effective dating is NOT applied in SQL: an expired grant is still returned,
    because filtering it is compile_authority's job and the `at` parameter would
    otherwise be a lie.

Skipped unless SKYLIZE_TEST_DB_URL (+ APP_DB_URL) are set.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
import pytest_asyncio

from skylize.app.principal.authority import compile_authority
from skylize.app.principal.models import GrantSource
from skylize.dal.connection import Database
from skylize.dal.principal import PgPrincipalRepository

from .conftest import APP_DB_URL, DB_URL, requires_app_role, requires_pg

pytestmark = pytest.mark.integration

#: Mirrors the CHECK on principal.authority_level (migration 0019:77-78).
_AUTHORITY_LEVELS = {"executive", "vp", "director", "manager", "worker"}

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _orgs() -> tuple[str, str]:
    s = uuid.uuid4().hex[:8]
    return f"prin_a_{s}", f"prin_b_{s}"


async def _seed_tenant(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _seed_principal(
    admin_conn,
    *,
    org: str,
    principal_id: str,
    authority_level: str = "worker",
    suspended: bool = False,
) -> None:
    await admin_conn.execute(
        """
        INSERT INTO principal (principal_id, org_id, display_name, position_id,
                               authority_level, manager_principal_id, suspended_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7)
        """,
        principal_id, org, f"Display {principal_id}", "pos_1",
        authority_level, None, (T0 if suspended else None),
    )


async def _seed_grant(
    admin_conn,
    *,
    org: str,
    principal_id: str,
    scope: str,
    source: str = "position",
    justification: str | None = None,
    valid_from: datetime = T0,
    valid_to: datetime | None = None,
) -> None:
    await admin_conn.execute(
        """
        INSERT INTO principal_grant (org_id, principal_id, scope, source,
                                     justification, valid_from, valid_to, created_by)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        """,
        org, principal_id, scope, source, justification, valid_from, valid_to, "test",
    )


async def _cleanup(admin_conn, orgs: list[str]) -> None:
    await admin_conn.execute(
        "DELETE FROM principal_grant WHERE org_id = ANY($1::text[])", orgs
    )
    await admin_conn.execute("DELETE FROM principal WHERE org_id = ANY($1::text[])", orgs)
    await admin_conn.execute("DELETE FROM tenants WHERE org_id = ANY($1::text[])", orgs)


@pytest_asyncio.fixture()
async def app_db(migrated_public: None) -> AsyncIterator[Database]:
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")
    db = Database(APP_DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

@requires_app_role
async def test_round_trip_principal_and_grants(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        await _seed_principal(
            admin_conn, org=org, principal_id="devon", authority_level="director"
        )
        await _seed_grant(admin_conn, org=org, principal_id="devon", scope="llm.generate")
        await _seed_grant(
            admin_conn, org=org, principal_id="devon", scope="stripe.refund",
            source="explicit_grant", justification="finance cover during Q3",
        )

        repo = PgPrincipalRepository(app_db)
        principal = await repo.load_principal(org_id=org, principal_id="devon")
        assert principal is not None
        assert principal.principal_id == "devon"
        assert principal.org_id == org
        assert principal.authority_level == "director"
        assert principal.position_id == "pos_1"
        assert principal.is_suspended is False

        grants = await repo.load_grants(org_id=org, principal_id="devon")
        assert {g.scope for g in grants} == {"llm.generate", "stripe.refund"}
        by_scope = {g.scope: g for g in grants}
        assert by_scope["llm.generate"].source is GrantSource.POSITION
        assert by_scope["stripe.refund"].source is GrantSource.EXPLICIT_GRANT
        assert by_scope["stripe.refund"].justification == "finance cover during Q3"

        # The models are extra="forbid"; created_at / grant_id / created_by exist
        # on the rows and must have been dropped, not passed through.
        snapshot = compile_authority(principal, grants, at=T0 + timedelta(days=1))
        assert snapshot.scopes == frozenset({"llm.generate", "stripe.refund"})
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_unknown_principal_reads_as_none_not_an_error(app_db, admin_conn) -> None:
    """Absence is a denial upstream (PrincipalNotFound), never a silent grant --
    but the repository's own job is simply to report absence honestly."""
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        repo = PgPrincipalRepository(app_db)
        assert await repo.load_principal(org_id=org, principal_id="nobody") is None
        assert list(await repo.load_grants(org_id=org, principal_id="nobody")) == []
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_expired_grants_are_returned_not_filtered_in_sql(app_db, admin_conn) -> None:
    """Effective dating belongs to compile_authority, not the query.

    If the SQL filtered on now(), the `at` parameter would be a lie and nobody
    could ask "what could this person do last Tuesday?".
    """
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        await _seed_principal(admin_conn, org=org, principal_id="devon")
        await _seed_grant(
            admin_conn, org=org, principal_id="devon", scope="expired.tool",
            valid_from=T0, valid_to=T0 + timedelta(days=1),
        )
        repo = PgPrincipalRepository(app_db)
        principal = await repo.load_principal(org_id=org, principal_id="devon")
        assert principal is not None
        grants = await repo.load_grants(org_id=org, principal_id="devon")

        # The row IS returned...
        assert {g.scope for g in grants} == {"expired.tool"}
        # ...and the pure kernel is what drops it, per instant.
        assert compile_authority(
            principal, grants, at=T0 + timedelta(hours=1)
        ).scopes == frozenset({"expired.tool"})
        assert compile_authority(
            principal, grants, at=T0 + timedelta(days=30)
        ).scopes == frozenset()
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# RLS — cross-tenant isolation as a non-superuser, non-owner role
# ---------------------------------------------------------------------------

@requires_app_role
async def test_rls_blocks_cross_tenant_principal_and_grant_reads(
    app_db, app_conn, admin_conn
) -> None:
    org_a, org_b = _orgs()
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
            await _seed_principal(admin_conn, org=org, principal_id="devon")
            await _seed_grant(
                admin_conn, org=org, principal_id="devon", scope=f"tool.{org}"
            )

        # Bound to org A, the app role sees ONLY org A's rows in BOTH tables --
        # an unfiltered SELECT, so the policy is what limits it, not a WHERE.
        async with app_db.tenant_session(org_a) as conn:
            principals = {r["org_id"] for r in await conn.fetch("SELECT org_id FROM principal")}
            grants = {r["org_id"] for r in await conn.fetch("SELECT org_id FROM principal_grant")}
        assert principals == {org_a}
        assert grants == {org_a}

        # And each org's own read still works through the repository.
        repo = PgPrincipalRepository(app_db)
        a = await repo.load_principal(org_id=org_a, principal_id="devon")
        b = await repo.load_principal(org_id=org_b, principal_id="devon")
        assert a is not None and a.org_id == org_a
        assert b is not None and b.org_id == org_b
        assert {g.scope for g in await repo.load_grants(org_id=org_a, principal_id="devon")} == {
            f"tool.{org_a}"
        }

        # The assertions that stop this test proving nothing.
        role = await app_conn.fetchval("SELECT current_user")
        rolrow = await admin_conn.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=$1", role
        )
        assert rolrow["rolsuper"] is False, f"{role} is a superuser - would bypass RLS"
        assert rolrow["rolbypassrls"] is False, f"{role} has BYPASSRLS - would bypass RLS"
        for table in ("principal", "principal_grant"):
            owner = await admin_conn.fetchval(
                "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE relname=$1", table
            )
            assert owner != role, f"{role} owns {table} - owner bypasses RLS unless FORCE"
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


# ---------------------------------------------------------------------------
# Migration shape — the facts dal/principal.py's mapping relies on
# ---------------------------------------------------------------------------

@requires_pg
async def test_authority_level_check_matches_the_literal(pg_schema: str) -> None:
    """dal/principal.py casts the column straight to the AuthorityLevel Literal.

    That cast is only honest if the CHECK constraint admits exactly the Literal's
    five values, so assert the agreement instead of trusting it.
    """
    conn = await asyncpg.connect(DB_URL)
    await conn.execute(f'SET search_path TO "{pg_schema}"')
    try:
        src = await conn.fetchval(
            "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
            "JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE t.relname='principal' AND n.nspname=$1 AND c.contype='c' "
            "AND pg_get_constraintdef(c.oid) LIKE '%authority_level%'",
            pg_schema,
        )
        assert src is not None, "no CHECK on principal.authority_level"
        for level in _AUTHORITY_LEVELS:
            assert f"'{level}'" in src, f"{level} missing from the CHECK"
    finally:
        await conn.close()


@requires_pg
async def test_justification_check_fires_in_the_database_too(pg_schema: str) -> None:
    """The Grant model validator (app/principal/models.py:98-106) and the DB CHECK
    (migration 0019:101-102) are deliberate belt-and-braces. Prove the DB half
    independently, since the model half never runs on an operator's INSERT."""
    conn = await asyncpg.connect(DB_URL)
    await conn.execute(f'SET search_path TO "{pg_schema}"')
    try:
        org = f"prin_chk_{uuid.uuid4().hex[:8]}"
        await conn.execute(
            "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3)",
            org, org, "https://issuer.example",
        )
        await conn.execute(
            "INSERT INTO principal (principal_id, org_id, display_name, authority_level) "
            "VALUES ($1,$2,$3,$4)",
            "devon", org, "Devon", "worker",
        )
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO principal_grant (org_id, principal_id, scope, source, "
                "justification, created_by) VALUES ($1,$2,$3,$4,$5,$6)",
                org, "devon", "stripe.refund", "explicit_grant", None, "test",
            )
    finally:
        await conn.close()
