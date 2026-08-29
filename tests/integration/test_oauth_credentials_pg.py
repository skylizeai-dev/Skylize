"""oauth_credentials integration tests — REAL Postgres, proven as the app role.

Covers the guarantees only a database can prove (migration 0021):
  * migration shape: FORCE RLS, tenant_isolation policy, the connection_state
    CHECK constraint, and the unique (org_id, provider, label) identity index;
  * **RLS cross-tenant isolation** — one org cannot read, update, or delete
    another org's OAuth grant, proven as a role that is neither a superuser nor
    the table owner (both would bypass RLS and make the test prove nothing);
  * round-trip of the structured columns through the real DAL, including the
    TEXT[] scopes array and the nullable refresh-token column;
  * on-demand refresh persisting through Postgres inside one transaction;
  * revocation writing durable, queryable connection_state.

Skipped unless SKYLIZE_TEST_DB_URL (+ SKYLIZE_TEST_APP_DB_URL) are set. The
provider used throughout is a SYNTHETIC name, never a real provider — this
infrastructure is provider-agnostic and no connector exists yet.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio

from skylize.app.audit.service import AuditService
from skylize.app.credentials.encryption import FernetEncryptor
from skylize.app.credentials.oauth import (
    GrantRevoked,
    OAuthCredentialService,
    OAuthProviderConfig,
)
from skylize.dal.connection import Database
from skylize.dal.memory import InMemoryAuditRepository
from skylize.dal.oauth_credentials import (
    OAuthCredentialRow,
    PgOAuthCredentialRepository,
)
from skylize.events.memory_bus import InMemoryEventBus

from .conftest import APP_DB_URL, TEST_CREDENTIAL_KEY, requires_app_role, requires_pg

pytestmark = pytest.mark.integration

PROVIDER = "acme_docs"  # SYNTHETIC — not a real provider


def _orgs() -> tuple[str, str]:
    s = uuid.uuid4().hex[:8]
    return f"oauth_a_{s}", f"oauth_b_{s}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _seed_tenant(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _cleanup(admin_conn, orgs: list[str]) -> None:
    await admin_conn.execute(
        "DELETE FROM oauth_credentials WHERE org_id = ANY($1::text[])", orgs
    )
    await admin_conn.execute("DELETE FROM tenants WHERE org_id = ANY($1::text[])", orgs)


@pytest_asyncio.fixture()
async def app_db(migrated_public: None):
    """A ``Database`` pool connected as the NON-SUPERUSER app role (RLS-subject)."""
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")
    db = Database(APP_DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


def _row(enc: FernetEncryptor, org: str, *, expires_in=timedelta(hours=1),
         refresh: str | None = "refresh-abc", label: str = "") -> OAuthCredentialRow:
    now = _now()
    return OAuthCredentialRow(
        cred_id=uuid.uuid4(),
        org_id=org,
        provider=PROVIDER,
        label=label,
        provider_account_id="acct-1",
        key_id="platform-fernet-v1",
        encrypted_access_token=enc.encrypt("access-old"),
        encrypted_refresh_token=enc.encrypt(refresh) if refresh is not None else None,
        expires_at=now + expires_in,
        scopes=("files.read", "files.write"),
        connection_state="valid",
        state_reason=None,
        created_at=now,
        updated_at=now,
        refreshed_at=None,
    )


def _service(repo, *, handler=None, providers=None) -> OAuthCredentialService:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return OAuthCredentialService(
        encryptor=FernetEncryptor(TEST_CREDENTIAL_KEY),
        repo=repo,
        audit=AuditService(InMemoryEventBus(), InMemoryAuditRepository()),
        providers=providers,
        http_client_factory=factory if handler is not None else None,
    )


# ---------------------------------------------------------------------------
# Migration shape
# ---------------------------------------------------------------------------

@requires_pg
async def test_migration_shape(admin_conn, pg_schema: str) -> None:
    rel = await admin_conn.fetchrow(
        "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname='oauth_credentials' AND n.nspname=$1",
        pg_schema,
    )
    assert rel is not None, "oauth_credentials table missing"
    assert rel["relrowsecurity"] is True, "RLS not ENABLEd"
    assert rel["relforcerowsecurity"] is True, "RLS not FORCEd — owner would bypass"

    pol = await admin_conn.fetchval(
        "SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname='oauth_credentials' AND n.nspname=$1 "
        "AND p.polname='tenant_isolation'",
        pg_schema,
    )
    assert pol == 1

    # The connection_state CHECK is what stops an invalid state being written.
    chk = await admin_conn.fetchval(
        "SELECT count(*) FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname='oauth_credentials' AND n.nspname=$1 "
        "AND con.conname='oauth_credentials_state_check'",
        pg_schema,
    )
    assert chk == 1

    idx = await admin_conn.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE tablename='oauth_credentials' "
        "AND indexname='idx_oauth_credentials_unique' AND schemaname=$1",
        pg_schema,
    )
    assert idx is not None and "UNIQUE" in idx


@requires_pg
async def test_connection_state_check_rejects_an_unknown_state(
    admin_conn, pg_schema: str
) -> None:
    org = f"oauth_chk_{uuid.uuid4().hex[:8]}"
    try:
        await _seed_tenant(admin_conn, org)
        with pytest.raises(Exception) as exc:
            await admin_conn.execute(
                "INSERT INTO oauth_credentials (org_id, provider, key_id, "
                "encrypted_access_token, expires_at, connection_state) "
                "VALUES ($1,$2,'k','ct',now(),'bogus_state')",
                org, PROVIDER,
            )
        assert "oauth_credentials_state_check" in str(exc.value)
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# RLS — the hard gate. Cross-tenant isolation as a genuine RLS subject.
# ---------------------------------------------------------------------------

@requires_app_role
async def test_rls_blocks_cross_tenant_grant_access(
    app_db, admin_conn, pg_schema: str
) -> None:
    """A grant written for org A must be invisible AND immutable from org B.

    Proven as `skylize_app`, which is asserted below to be neither a superuser
    nor the table owner — either would bypass RLS and make this test vacuous.
    """
    org_a, org_b = _orgs()
    enc = FernetEncryptor(TEST_CREDENTIAL_KEY)
    repo = PgOAuthCredentialRepository(app_db)
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)

        row_a = _row(enc, org_a)
        await repo.insert(row_a)

        # A sees its own grant.
        assert (await repo.get(org_a, PROVIDER, "")) is not None
        # B sees nothing at all.
        assert (await repo.get(org_b, PROVIDER, "")) is None
        assert await repo.list_for_org(org_b) == []

        # B cannot mutate A's row even naming A's cred_id explicitly: the row is
        # outside B's policy scope, so the UPDATE matches zero rows.
        changed = await repo.set_connection_state(
            cred_id=row_a.cred_id, org_id=org_b, state="revoked", reason="attack",
        )
        assert changed is False
        still = await repo.get(org_a, PROVIDER, "")
        assert still.connection_state == "valid", "cross-tenant write must not land"

        # And a raw cross-tenant SELECT under B's binding returns nothing even
        # with no org predicate in the SQL — proving the POLICY is doing the work,
        # not just the DAL's redundant WHERE clause.
        async with app_db.tenant_session(org_b) as conn:
            leaked = await conn.fetch("SELECT cred_id FROM oauth_credentials")
            assert leaked == [], "RLS policy leaked rows across tenants"

        # The role must genuinely be an RLS subject or none of the above proves
        # anything.
        role = await admin_conn.fetchval(
            "SELECT rolname FROM pg_roles WHERE rolname='skylize_app'"
        )
        assert role == "skylize_app"
        rolrow = await admin_conn.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=$1", role
        )
        assert rolrow["rolsuper"] is False, f"{role} is a superuser — would bypass RLS"
        assert rolrow["rolbypassrls"] is False, f"{role} has BYPASSRLS"
        owner = await admin_conn.fetchval(
            "SELECT pg_get_userbyid(relowner) FROM pg_class "
            "WHERE relname='oauth_credentials'"
        )
        assert owner != role, f"{role} owns the table — owner bypasses RLS unless FORCE"
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


# ---------------------------------------------------------------------------
# DAL round-trip through the real structured columns
# ---------------------------------------------------------------------------

@requires_app_role
async def test_structured_columns_round_trip(app_db, admin_conn) -> None:
    org, _ = _orgs()
    enc = FernetEncryptor(TEST_CREDENTIAL_KEY)
    repo = PgOAuthCredentialRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        row = _row(enc, org)
        await repo.insert(row)

        got = await repo.get(org, PROVIDER, "")
        assert got.scopes == ("files.read", "files.write"), "TEXT[] must round-trip"
        assert got.key_id == "platform-fernet-v1"
        assert got.provider_account_id == "acct-1"
        assert enc.decrypt(got.encrypted_access_token) == "access-old"
        assert enc.decrypt(got.encrypted_refresh_token) == "refresh-abc"
        assert got.connection_state == "valid"
        # expires_at is a real column, readable WITHOUT decrypting anything —
        # the property that makes the freshness check possible at all.
        assert got.expires_at > _now()
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_nullable_refresh_token_round_trips(app_db, admin_conn) -> None:
    """Providers that issue no refresh token must be storable."""
    org, _ = _orgs()
    enc = FernetEncryptor(TEST_CREDENTIAL_KEY)
    repo = PgOAuthCredentialRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        await repo.insert(_row(enc, org, refresh=None))
        got = await repo.get(org, PROVIDER, "")
        assert got.encrypted_refresh_token is None
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_unique_identity_allows_two_labels_one_provider(
    app_db, admin_conn
) -> None:
    """(org, provider, label) is the identity: a second connection to the same
    provider coexists under a distinct label."""
    org, _ = _orgs()
    enc = FernetEncryptor(TEST_CREDENTIAL_KEY)
    repo = PgOAuthCredentialRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        await repo.insert(_row(enc, org, label=""))
        await repo.insert(_row(enc, org, label="secondary"))
        assert len(await repo.list_for_org(org)) == 2
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# On-demand refresh through real Postgres
# ---------------------------------------------------------------------------

@requires_app_role
async def test_refresh_persists_through_postgres(app_db, admin_conn) -> None:
    org, _ = _orgs()
    enc = FernetEncryptor(TEST_CREDENTIAL_KEY)
    repo = PgOAuthCredentialRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        await repo.insert(_row(enc, org, expires_in=timedelta(minutes=-10)))

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "access_token": "access-new",
                "expires_in": 3600,
                "refresh_token": "refresh-rotated",
            })

        svc = _service(repo, handler=handler, providers={
            PROVIDER: OAuthProviderConfig(
                provider=PROVIDER, token_url="https://acme.test/token",
                client_id="cid", client_secret="secret",
            )
        })
        await svc.ensure_fresh(org_id=org, provider=PROVIDER)

        stored = await repo.get(org, PROVIDER, "")
        assert enc.decrypt(stored.encrypted_access_token) == "access-new"
        assert enc.decrypt(stored.encrypted_refresh_token) == "refresh-rotated"
        assert stored.expires_at > _now()
        assert stored.refreshed_at is not None
        assert stored.connection_state == "valid"
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_revocation_writes_queryable_connection_state(
    app_db, admin_conn
) -> None:
    """The state a future reconnect UI reads must be durable and queryable."""
    org, _ = _orgs()
    enc = FernetEncryptor(TEST_CREDENTIAL_KEY)
    repo = PgOAuthCredentialRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        await repo.insert(_row(enc, org, expires_in=timedelta(minutes=-10)))

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "invalid_grant"})

        svc = _service(repo, handler=handler, providers={
            PROVIDER: OAuthProviderConfig(
                provider=PROVIDER, token_url="https://acme.test/token",
                client_id="cid", client_secret="secret",
            )
        })
        with pytest.raises(GrantRevoked):
            await svc.ensure_fresh(org_id=org, provider=PROVIDER)

        stored = await repo.get(org, PROVIDER, "")
        assert stored.connection_state == "revoked"
        assert stored.state_reason is not None
        # Queryable as a plain predicate — no JSONB extraction needed.
        async with app_db.tenant_session(org) as conn:
            n = await conn.fetchval(
                "SELECT count(*) FROM oauth_credentials "
                "WHERE org_id=$1 AND connection_state='revoked'",
                org,
            )
        assert n == 1
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_transient_failure_leaves_state_untouched_in_pg(
    app_db, admin_conn
) -> None:
    """The destructive-write asymmetry, proven against the real table."""
    org, _ = _orgs()
    enc = FernetEncryptor(TEST_CREDENTIAL_KEY)
    repo = PgOAuthCredentialRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        await repo.insert(_row(enc, org, expires_in=timedelta(minutes=-10)))

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "server_error"})

        svc = _service(repo, handler=handler, providers={
            PROVIDER: OAuthProviderConfig(
                provider=PROVIDER, token_url="https://acme.test/token",
                client_id="cid", client_secret="secret",
            )
        })
        with pytest.raises(Exception):
            await svc.ensure_fresh(org_id=org, provider=PROVIDER)

        stored = await repo.get(org, PROVIDER, "")
        assert stored.connection_state == "valid", (
            "a 503 must never mark a customer's grant revoked"
        )
    finally:
        await _cleanup(admin_conn, [org])
