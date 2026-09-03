"""Migration 0023 (nullable ``expires_at``) against REAL Postgres, as the app role.

Only a database can prove the three things here:

  * the NOT NULL constraint is genuinely gone — a NULL insert SUCCEEDS rather
    than raising, which is the whole point of the migration;
  * a NULL survives a full DAL round trip as NULL, and is not coerced into a
    timestamp or dropped somewhere between Python and Postgres;
  * every guarantee migration 0021 established is INTACT — RLS still forced, the
    tenant_isolation policy still present, the connection_state CHECK still
    rejecting bad values, and the (org_id, provider, label) identity index still
    unique. Relaxing one column's nullability must not have weakened anything
    else, and asserting that is cheaper than assuming it.

The provider name is SYNTHETIC. This pass extends the infrastructure only; no
Notion connector exists, so nothing here names a real provider.

Skipped unless SKYLIZE_TEST_DB_URL (+ SKYLIZE_TEST_APP_DB_URL) are set.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from skylize.app.credentials.encryption import FernetEncryptor
from skylize.app.credentials.oauth import GrantStatus, evaluate_grant
from skylize.dal.connection import Database
from skylize.dal.oauth_credentials import (
    OAuthCredentialRow,
    PgOAuthCredentialRepository,
)

from .conftest import APP_DB_URL, TEST_CREDENTIAL_KEY, requires_app_role, requires_pg

pytestmark = pytest.mark.integration

PROVIDER = "synthetic_never_expires"  # SYNTHETIC — not a real provider


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _org() -> str:
    return f"oauth_null_{uuid.uuid4().hex[:8]}"


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


def _row(
    enc: FernetEncryptor, org: str, *, expires_at: datetime | None, label: str = ""
) -> OAuthCredentialRow:
    now = _now()
    return OAuthCredentialRow(
        cred_id=uuid.uuid4(),
        org_id=org,
        provider=PROVIDER,
        label=label,
        provider_account_id="acct-1",
        key_id="platform-fernet-v1",
        encrypted_access_token=enc.encrypt("access-tok"),
        encrypted_refresh_token=enc.encrypt("refresh-tok"),
        expires_at=expires_at,
        scopes=(),
        connection_state="valid",
        state_reason=None,
        created_at=now,
        updated_at=now,
        refreshed_at=None,
    )


# ---------------------------------------------------------------------------
# The constraint is actually gone
# ---------------------------------------------------------------------------

@requires_pg
async def test_expires_at_is_nullable_in_the_live_schema(admin_conn, pg_schema: str) -> None:
    """Read the catalog directly rather than inferring from a successful insert."""
    is_nullable = await admin_conn.fetchval(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_schema = $1 AND table_name = 'oauth_credentials' "
        "AND column_name = 'expires_at'",
        pg_schema,
    )
    assert is_nullable == "YES", "migration 0023 did not drop the NOT NULL constraint"


@requires_pg
async def test_raw_null_insert_succeeds(migrated_public, admin_conn) -> None:
    """`migrated_public` is required: `admin_conn` talks to the REAL public
    schema, which only carries 0023 once migrations have been upgraded there.
    Without it this asserts against whatever DDL the database happened to have."""
    org = _org()
    try:
        await _seed_tenant(admin_conn, org)
        await admin_conn.execute(
            "INSERT INTO oauth_credentials "
            "(org_id, provider, label, key_id, encrypted_access_token, expires_at) "
            "VALUES ($1,$2,$3,$4,$5,NULL)",
            org, PROVIDER, "", "platform-fernet-v1", "ciphertext",
        )
        stored = await admin_conn.fetchval(
            "SELECT expires_at FROM oauth_credentials WHERE org_id = $1", org
        )
        assert stored is None
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# NULL survives a real DAL round trip, as the RLS-subject app role
# ---------------------------------------------------------------------------

@requires_app_role
async def test_null_expiry_round_trips_through_the_dal(app_db, admin_conn) -> None:
    org = _org()
    enc = FernetEncryptor(TEST_CREDENTIAL_KEY)
    repo = PgOAuthCredentialRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        await repo.insert(_row(enc, org, expires_at=None))

        got = await repo.get(org, PROVIDER, "")
        assert got is not None
        assert got.expires_at is None, "NULL was coerced somewhere in the round trip"
        # And the evaluator reaches the right verdict on a REAL stored row, not
        # just on a hand-built one.
        assert evaluate_grant(got, now=_now()) is GrantStatus.VALID
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_expiring_and_non_expiring_grants_coexist(app_db, admin_conn) -> None:
    """The migration widened the domain; it did not replace it. Drive/Asana-shaped
    rows and Notion-shaped rows must live in the same table simultaneously."""
    org = _org()
    enc = FernetEncryptor(TEST_CREDENTIAL_KEY)
    repo = PgOAuthCredentialRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        await repo.insert(_row(enc, org, expires_at=None, label="never"))
        await repo.insert(
            _row(enc, org, expires_at=_now() + timedelta(hours=1), label="expiring")
        )

        never = await repo.get(org, PROVIDER, "never")
        expiring = await repo.get(org, PROVIDER, "expiring")
        assert never is not None and expiring is not None
        assert never.expires_at is None
        assert expiring.expires_at is not None and expiring.expires_at > _now()
        assert evaluate_grant(never, now=_now()) is GrantStatus.VALID
        assert evaluate_grant(expiring, now=_now()) is GrantStatus.VALID
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_update_tokens_can_write_a_null_expiry(app_db, admin_conn) -> None:
    """A grant that had an expiry and is refreshed by a provider that issues none
    must end up NULL, not stuck with its old timestamp."""
    org = _org()
    enc = FernetEncryptor(TEST_CREDENTIAL_KEY)
    repo = PgOAuthCredentialRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        row = _row(enc, org, expires_at=_now() + timedelta(hours=1))
        await repo.insert(row)

        async with repo.tenant_session(org) as conn:
            locked = await repo.get_for_update(conn, org, PROVIDER, "")
            assert locked is not None
            await repo.update_tokens(
                conn=conn,
                cred_id=locked.cred_id,
                org_id=org,
                encrypted_access_token=enc.encrypt("new-access"),
                encrypted_refresh_token=locked.encrypted_refresh_token,
                expires_at=None,
                key_id="platform-fernet-v1",
                refreshed_at=_now(),
            )

        got = await repo.get(org, PROVIDER, "")
        assert got is not None
        assert got.expires_at is None
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# Everything migration 0021 guaranteed is still true
# ---------------------------------------------------------------------------

@requires_pg
async def test_0021_guarantees_survive_the_relaxation(admin_conn, pg_schema: str) -> None:
    """Relaxing one column must not have weakened RLS, the policy, or the CHECK."""
    rel = await admin_conn.fetchrow(
        "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname='oauth_credentials' AND n.nspname=$1",
        pg_schema,
    )
    assert rel is not None
    assert rel["relrowsecurity"] is True, "RLS no longer ENABLEd"
    assert rel["relforcerowsecurity"] is True, "RLS no longer FORCEd — owner bypasses"

    pol = await admin_conn.fetchval(
        "SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname='oauth_credentials' AND n.nspname=$1 "
        "AND p.polname='tenant_isolation'",
        pg_schema,
    )
    assert pol == 1, "tenant_isolation policy missing"

    chk = await admin_conn.fetchval(
        "SELECT count(*) FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname='oauth_credentials' AND n.nspname=$1 "
        "AND con.conname='oauth_credentials_state_check'",
        pg_schema,
    )
    assert chk == 1, "connection_state CHECK constraint missing"


@requires_pg
async def test_connection_state_check_still_rejects_bad_values(
    migrated_public, admin_conn
) -> None:
    """A non-expiring grant's liveness rests ENTIRELY on connection_state, so this
    constraint matters more after 0023, not less."""
    org = _org()
    try:
        await _seed_tenant(admin_conn, org)
        with pytest.raises(Exception):
            await admin_conn.execute(
                "INSERT INTO oauth_credentials "
                "(org_id, provider, label, key_id, encrypted_access_token, "
                " expires_at, connection_state) "
                "VALUES ($1,$2,$3,$4,$5,NULL,'not_a_state')",
                org, PROVIDER, "", "platform-fernet-v1", "ciphertext",
            )
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_rls_still_isolates_a_non_expiring_grant(app_db, admin_conn) -> None:
    """Tenant isolation must hold for NULL-expiry rows exactly as for others."""
    org_a, org_b = _org(), _org()
    enc = FernetEncryptor(TEST_CREDENTIAL_KEY)
    repo = PgOAuthCredentialRepository(app_db)
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        await repo.insert(_row(enc, org_a, expires_at=None))

        assert await repo.get(org_a, PROVIDER, "") is not None
        assert await repo.get(org_b, PROVIDER, "") is None, (
            "org B read org A's non-expiring grant — RLS is not isolating"
        )
    finally:
        await _cleanup(admin_conn, [org_a, org_b])
