"""Principal provisioning on REAL Postgres — including what must still fail.

Two properties this suite exists to prove, which the in-memory unit tests
structurally cannot:

1. The principal write survives ROW LEVEL SECURITY. `principal` and
   `principal_grant` carry ENABLE + FORCE RLS whose WITH CHECK requires
   `skylize.org_id` (migration 0019). `admin_session` never sets that GUC, so a
   write issued there is refused for a non-superuser role — which is exactly why
   `provision_owner_principal` uses `tenant_session`. These cases run as the
   non-superuser `skylize_app` role, so the policy is genuinely in force rather
   than bypassed.

2. Registration into an org with NO tenant row still fails, unchanged. That
   failure is the FK `users.org_id -> tenants(org_id)` (migration 0001), and it
   is the thing that keeps self-service org creation closed. If this change had
   accidentally made end-to-end registration "just work", it would have silently
   reopened the declined Option 1 — so the absence of that fix is asserted here
   as a requirement, not left to inspection.

Skipped unless the Postgres env vars are set. `SKYLIZE_TEST_APP_DB_URL` MUST be
the non-superuser, non-table-owner role or the RLS assertions prove nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from skylize.app.principal.models import COWORK_SEED_MANIFEST
from skylize.dal.connection import Database
from skylize.dal.ports import UserRow
from skylize.dal.principal import PgPrincipalRepository
from skylize.dal.users import PgUserRepository

from .conftest import APP_DB_URL, requires_app_role

pytestmark = pytest.mark.integration


def _org() -> str:
    return f"prov_{uuid.uuid4().hex[:8]}"


def _user(org: str, *, email: str) -> UserRow:
    return UserRow(
        user_id=uuid.uuid4(),
        org_id=org,
        email=email,
        password_hash="not-a-real-hash",
        display_name=None,
        roles=["owner"],
        is_active=True,
        created_at=datetime.now(timezone.utc),
        last_login_at=None,
    )


@pytest_asyncio.fixture()
async def app_db(migrated_public: None) -> AsyncIterator[Database]:
    """A `Database` bound to the RLS-SUBJECT app role, not the superuser.

    The whole point of these cases is that the tenant policy is enforced, and a
    superuser or table owner bypasses RLS regardless of FORCE.
    """
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")
    database = Database(APP_DB_URL)
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


async def _seed_tenant(conn: Any, org: str) -> None:
    """What an OPERATOR does out-of-band today, via
    `python -m skylize.ops.bootstrap_api_key --create-tenant`."""
    await conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _cleanup(conn: Any, org: str) -> None:
    for sql in (
        "DELETE FROM principal_grant WHERE org_id=$1",
        "DELETE FROM principal WHERE org_id=$1",
        "DELETE FROM users WHERE org_id=$1",
        "DELETE FROM tenants WHERE org_id=$1",
    ):
        try:
            await conn.execute(sql, org)
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass


@requires_app_role
async def test_provisioning_writes_principal_and_grants_under_rls(
    app_db, admin_conn
) -> None:
    """The happy path an owner of an operator-created org actually takes."""
    org = _org()
    users = PgUserRepository(app_db)
    principals = PgPrincipalRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        row = _user(org, email=f"{org}@example.com")
        assert await users.create_owner_of_new_org(row)

        created = await principals.provision_owner_principal(
            org_id=org, principal_id=str(row.user_id), display_name="Owner"
        )
        assert created is True

        principal = await principals.load_principal(
            org_id=org, principal_id=str(row.user_id)
        )
        assert principal is not None
        assert principal.principal_id == str(row.user_id)
        assert principal.authority_level == "executive"

        grants = await principals.load_grants(
            org_id=org, principal_id=str(row.user_id)
        )
        assert sorted(g.scope for g in grants) == sorted(COWORK_SEED_MANIFEST)
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_provisioning_is_idempotent_on_postgres(app_db, admin_conn) -> None:
    """Re-running converges: no duplicate grants, no constraint violation.

    This is the property that replaces atomicity — the user write and this one
    cannot share a transaction, so a crash between them is repaired by calling
    again.
    """
    org = _org()
    users = PgUserRepository(app_db)
    principals = PgPrincipalRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        row = _user(org, email=f"{org}@example.com")
        assert await users.create_owner_of_new_org(row)
        pid = str(row.user_id)

        assert await principals.provision_owner_principal(
            org_id=org, principal_id=pid, display_name="Owner"
        ) is True
        assert await principals.provision_owner_principal(
            org_id=org, principal_id=pid, display_name="Owner"
        ) is False

        grants = await principals.load_grants(org_id=org, principal_id=pid)
        assert sorted(g.scope for g in grants) == sorted(COWORK_SEED_MANIFEST), (
            "a second call must not duplicate grants"
        )
        count = await admin_conn.fetchval(
            "SELECT count(*) FROM principal_grant WHERE org_id=$1", org
        )
        assert count == len(COWORK_SEED_MANIFEST)
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_torn_state_is_repaired_by_calling_again(app_db, admin_conn) -> None:
    """Principal landed, grants did not — the exact crash-torn state.

    A principal with no grants is a person the kernel knows who can do nothing,
    so the grant insert must not be conditional on having created the principal.
    """
    org = _org()
    principals = PgPrincipalRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        row = _user(org, email=f"{org}@example.com")
        await PgUserRepository(app_db).create_owner_of_new_org(row)
        pid = str(row.user_id)

        await principals.provision_owner_principal(
            org_id=org, principal_id=pid, display_name="Owner"
        )
        await admin_conn.execute(
            "DELETE FROM principal_grant WHERE org_id=$1 AND principal_id=$2", org, pid
        )

        created = await principals.provision_owner_principal(
            org_id=org, principal_id=pid, display_name="Owner"
        )

        assert created is False, "the principal row already existed"
        grants = await principals.load_grants(org_id=org, principal_id=pid)
        assert sorted(g.scope for g in grants) == sorted(COWORK_SEED_MANIFEST)
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_provisioning_creates_no_spend_envelope(app_db, admin_conn) -> None:
    """A principal existing must NOT imply a configured budget.

    `spend_envelope` carries a ceiling and an `over_ceiling_behavior` that are
    governance decisions someone has to actually make. Asserted against the table
    directly rather than against the code, so an envelope written by any future
    edit to this path is caught.
    """
    org = _org()
    principals = PgPrincipalRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        row = _user(org, email=f"{org}@example.com")
        await PgUserRepository(app_db).create_owner_of_new_org(row)
        await principals.provision_owner_principal(
            org_id=org, principal_id=str(row.user_id), display_name="Owner"
        )

        envelopes = await admin_conn.fetchval(
            "SELECT count(*) FROM spend_envelope WHERE org_id=$1", org
        )
        assert envelopes == 0
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_principal_is_invisible_to_another_org(app_db, admin_conn) -> None:
    """Tenant isolation holds at the data layer, as the RLS-subject role."""
    org_a, org_b = _org(), _org()
    principals = PgPrincipalRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org_a)
        await _seed_tenant(admin_conn, org_b)
        row = _user(org_a, email=f"{org_a}@example.com")
        await PgUserRepository(app_db).create_owner_of_new_org(row)
        await principals.provision_owner_principal(
            org_id=org_a, principal_id=str(row.user_id), display_name="Owner"
        )

        assert await principals.load_principal(
            org_id=org_b, principal_id=str(row.user_id)
        ) is None
    finally:
        await _cleanup(admin_conn, org_a)
        await _cleanup(admin_conn, org_b)


# ---------------------------------------------------------------------------
# The negative property: what must still be impossible.
# ---------------------------------------------------------------------------


@requires_app_role
async def test_registration_without_a_tenant_still_fails_with_the_fk(
    app_db, admin_conn
) -> None:
    """UNCHANGED BEHAVIOUR, asserted as a requirement.

    Registering into an org that has no `tenants` row must still raise
    ForeignKeyViolationError from `users.org_id -> tenants(org_id)` (migration
    0001). This is the deadlock documented in ops/bootstrap_api_key.py, and it is
    what keeps unauthenticated org creation closed.

    If this test ever fails because registration succeeded, the declined Option 1
    has been reopened by accident — someone made self-service tenant creation
    work. That is a governance change, not a bug fix, and it must not land as a
    side effect.
    """
    org = _org()  # deliberately NOT seeded into `tenants`
    users = PgUserRepository(app_db)
    try:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await users.create_owner_of_new_org(_user(org, email=f"{org}@example.com"))

        # And nothing was provisioned as a consolation prize.
        assert await admin_conn.fetchval(
            "SELECT count(*) FROM principal WHERE org_id=$1", org
        ) == 0
        assert await admin_conn.fetchval(
            "SELECT count(*) FROM users WHERE org_id=$1", org
        ) == 0
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_principal_write_is_refused_without_a_tenant_binding(
    app_db, admin_conn
) -> None:
    """Why `provision_owner_principal` must use `tenant_session`.

    Issued on a connection with no `skylize.org_id` set — which is what
    `admin_session` gives you — the same INSERT is refused by the
    `tenant_isolation` policy's WITH CHECK. This pins the reason for the design
    so a future "simplification" to `admin_session` fails loudly here instead of
    silently at runtime.
    """
    org = _org()
    try:
        await _seed_tenant(admin_conn, org)
        row = _user(org, email=f"{org}@example.com")
        await PgUserRepository(app_db).create_owner_of_new_org(row)

        unbound = await asyncpg.connect(APP_DB_URL)
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await unbound.execute(
                    "INSERT INTO principal (principal_id, org_id, display_name, "
                    "authority_level) VALUES ($1,$2,$3,'executive')",
                    str(row.user_id), org, "Owner",
                )
        finally:
            await unbound.close()
    finally:
        await _cleanup(admin_conn, org)
