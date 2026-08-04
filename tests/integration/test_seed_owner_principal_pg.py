"""Migration 0020's seed, against REAL Postgres.

The claim that needs measuring is idempotency. 0020 is written to be safe when
re-run -- Alembic normally runs a migration once, but an operator who has already
provisioned the same owner by hand, or a re-run against a partially seeded
database, must not produce a duplicate principal or a second identical grant.
`principal_grant` has no natural unique key (grant_id defaults to
gen_random_uuid()), so nothing in the schema would stop a duplicate; only the
WHERE NOT EXISTS in the migration does. That is exactly the kind of claim that
has to be executed rather than reasoned about.

The seed statements are re-executed here against a disposable schema rather than
re-running Alembic, so the assertion is about the SQL itself and not about
Alembic's version bookkeeping (which would simply refuse a second run and prove
nothing).

Also pinned: the identity derivation. `principal_id` must equal
`users.user_id::text`, because RequestContext.user_id is the JWT `sub`
(edge/deps.py:75) minted from users.user_id (edge/routes/auth.py:136). If those
two ever diverge, a principal lookup from a request context stops resolving and
the per-employee shape silently fails closed for everyone.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from .conftest import DB_URL, requires_pg

pytestmark = pytest.mark.integration

_MANIFEST = ("llm.generate", "memory.search")

# The two statements migration 0020 runs, verbatim in shape.
_SEED_PRINCIPAL = """
    INSERT INTO principal (principal_id, org_id, display_name, authority_level)
    SELECT u.user_id::text, u.org_id,
           COALESCE(NULLIF(btrim(u.display_name), ''), u.email),
           'executive'
      FROM users u
     WHERE 'owner' = ANY (u.roles)
    ON CONFLICT (org_id, principal_id) DO NOTHING
"""

_SEED_GRANT = """
    INSERT INTO principal_grant (org_id, principal_id, scope, source, created_by)
    SELECT p.org_id, p.principal_id, $1, 'position', 'seed'
      FROM principal p
     WHERE NOT EXISTS (
               SELECT 1 FROM principal_grant g
                WHERE g.org_id = p.org_id
                  AND g.principal_id = p.principal_id
                  AND g.scope = $1
                  AND g.source = 'position'
           )
"""


async def _run_seed(conn) -> None:
    await conn.execute(_SEED_PRINCIPAL)
    for scope in _MANIFEST:
        await conn.execute(_SEED_GRANT, scope)


async def _make_owner(conn, org: str, email: str) -> uuid.UUID:
    await conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3)",
        org, org, "https://issuer.example",
    )
    user_id: uuid.UUID = await conn.fetchval(
        "INSERT INTO users (org_id, email, password_hash, display_name, roles) "
        "VALUES ($1,$2,$3,$4,$5) RETURNING user_id",
        org, email, "x", "Owner Person", ["owner"],
    )
    return user_id


@requires_pg
async def test_seed_is_a_no_op_the_second_time(pg_schema: str) -> None:
    conn = await asyncpg.connect(DB_URL)
    await conn.execute(f'SET search_path TO "{pg_schema}"')
    try:
        org = f"seed_{uuid.uuid4().hex[:8]}"
        await _make_owner(conn, org, f"{org}@example.com")

        await _run_seed(conn)
        principals_1 = await conn.fetchval("SELECT count(*) FROM principal")
        grants_1 = await conn.fetchval("SELECT count(*) FROM principal_grant")
        assert principals_1 == 1
        assert grants_1 == len(_MANIFEST)

        # The whole point: re-running must insert nothing and raise nothing.
        await _run_seed(conn)
        assert await conn.fetchval("SELECT count(*) FROM principal") == principals_1
        assert await conn.fetchval("SELECT count(*) FROM principal_grant") == grants_1
    finally:
        await conn.close()


@requires_pg
async def test_principal_id_is_the_users_user_id_as_text(pg_schema: str) -> None:
    """The identity decision 0020 encodes. A request context carries the JWT
    `sub`, which is users.user_id; if the seed derived principal_id any other
    way, no request could ever resolve to a principal."""
    conn = await asyncpg.connect(DB_URL)
    await conn.execute(f'SET search_path TO "{pg_schema}"')
    try:
        org = f"seed_{uuid.uuid4().hex[:8]}"
        user_id = await _make_owner(conn, org, f"{org}@example.com")
        await _run_seed(conn)

        row = await conn.fetchrow(
            "SELECT principal_id, authority_level, display_name FROM principal "
            "WHERE org_id = $1",
            org,
        )
        assert row["principal_id"] == str(user_id)
        assert row["authority_level"] == "executive"
        assert row["display_name"] == "Owner Person"
    finally:
        await conn.close()


@requires_pg
async def test_only_owners_are_seeded(pg_schema: str) -> None:
    """A non-owner user must NOT get a principal. The seed grants the co-work
    manifest, so seeding every user would hand it to viewers as well."""
    conn = await asyncpg.connect(DB_URL)
    await conn.execute(f'SET search_path TO "{pg_schema}"')
    try:
        org = f"seed_{uuid.uuid4().hex[:8]}"
        owner_id = await _make_owner(conn, org, f"owner-{org}@example.com")
        await conn.execute(
            "INSERT INTO users (org_id, email, password_hash, roles) "
            "VALUES ($1,$2,$3,$4)",
            org, f"viewer-{org}@example.com", "x", ["viewer"],
        )
        await _run_seed(conn)

        ids = [r["principal_id"] for r in await conn.fetch("SELECT principal_id FROM principal")]
        assert ids == [str(owner_id)]
    finally:
        await conn.close()


@requires_pg
async def test_seeded_grant_is_exactly_the_cowork_manifest(pg_schema: str) -> None:
    """Not "every scope": the seed makes ONE agent usable by the org owner, it
    does not mint a superuser."""
    from skylize.contracts.mvp.cowork import cowork_agent

    conn = await asyncpg.connect(DB_URL)
    await conn.execute(f'SET search_path TO "{pg_schema}"')
    try:
        org = f"seed_{uuid.uuid4().hex[:8]}"
        await _make_owner(conn, org, f"{org}@example.com")
        await _run_seed(conn)

        scopes = {
            r["scope"] for r in await conn.fetch("SELECT scope FROM principal_grant")
        }
        assert scopes == {g.tool_id for g in cowork_agent.allowed_tools}
        assert scopes == set(_MANIFEST)

        sources = {
            r["source"] for r in await conn.fetch("SELECT source FROM principal_grant")
        }
        # 'position' needs no justification; the DB CHECK only demands one for
        # explicit_grant / explicit_deny (migration 0019:101-102).
        assert sources == {"position"}
    finally:
        await conn.close()


@requires_pg
async def test_seed_on_an_empty_database_is_a_legitimate_no_op(pg_schema: str) -> None:
    """0020 must run on a fresh database, where there are no tenants and no
    users -- principal.org_id is a FK, so a hardcoded row could not exist."""
    conn = await asyncpg.connect(DB_URL)
    await conn.execute(f'SET search_path TO "{pg_schema}"')
    try:
        await _run_seed(conn)
        assert await conn.fetchval("SELECT count(*) FROM principal") == 0
        assert await conn.fetchval("SELECT count(*) FROM principal_grant") == 0
    finally:
        await conn.close()
