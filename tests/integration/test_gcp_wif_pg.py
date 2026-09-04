"""gcp_wif_connections / gcp_wif_targets — REAL Postgres, proven as the app role.

Covers the guarantees only a database can prove (migration 0024):
  * migration shape: FORCE RLS and a `tenant_isolation` policy on BOTH tables,
    every CHECK constraint, and the two uniqueness guarantees;
  * **RLS cross-tenant isolation** — one org cannot read, update, or delete
    another org's federation trust or its target list, proven as a role that is
    neither a superuser nor the table owner (either would bypass RLS and make the
    test prove nothing);
  * the globally-unique issuer slug, which is a TENANCY control rather than a
    convenience: a collision would let one org's Google provider accept another
    org's assertions;
  * the access_mode/service_account biconditional, which stops a half-configured
    impersonation trust being stored at all;
  * DAL round-trip and probe-state persistence through the real columns.

Skipped unless SKYLIZE_TEST_DB_URL (+ SKYLIZE_TEST_APP_DB_URL) are set.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from skylize.dal.connection import Database
from skylize.dal.gcp_wif import (
    GcpWifConnectionRow,
    GcpWifTargetRow,
    PgGcpWifRepository,
)

from .conftest import APP_DB_URL, requires_app_role, requires_pg

pytestmark = pytest.mark.integration

# FIXTURE CHOICE MATTERS HERE, and getting it wrong produces an order-dependent
# flake rather than an honest failure:
#   * `pg_schema`       — creates a throwaway schema and migrates INTO it. Use it
#                         for tests that inspect catalogue metadata (pg_class,
#                         pg_policy, pg_constraint) scoped to that schema.
#   * `migrated_public` — migrates the REAL `public` schema. Use it for any test
#                         that writes through `admin_conn` or `app_db`, because
#                         both connect with the default search_path and therefore
#                         touch `public`, not the throwaway schema.
# Three constraint tests below originally declared `pg_schema` while writing via
# `admin_conn`. They passed only when some earlier test had already migrated
# `public`, and failed on a fresh database.


def _orgs() -> tuple[str, str]:
    s = uuid.uuid4().hex[:8]
    return f"wif_a_{s}", f"wif_b_{s}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug() -> str:
    return uuid.uuid4().hex[:26].ljust(26, "z")


async def _seed_tenant(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _cleanup(admin_conn, orgs: list[str]) -> None:
    await admin_conn.execute(
        "DELETE FROM gcp_wif_targets WHERE org_id = ANY($1::text[])", orgs
    )
    await admin_conn.execute(
        "DELETE FROM gcp_wif_connections WHERE org_id = ANY($1::text[])", orgs
    )


def _conn_row(org: str, *, label: str = "", slug: str | None = None,
              state: str = "unverified") -> GcpWifConnectionRow:
    now = _now()
    return GcpWifConnectionRow(
        conn_id=uuid.uuid4(),
        org_id=org,
        label=label,
        issuer_slug=slug or _slug(),
        gcp_project_id="customer-prod",
        gcp_project_number="123456789012",
        workload_identity_pool_id="skylize-pool",
        workload_identity_provider_id="skylize-provider",
        audience="//iam.googleapis.com/projects/123456789012/locations/global/"
                 "workloadIdentityPools/skylize-pool/providers/skylize-provider",
        access_mode="direct",
        service_account_email=None,
        signing_key_id="wif-es256-v1",
        jwks_delivery="served",
        expected_jwks_key_id=None,
        connection_state=state,  # type: ignore[arg-type]
        state_reason=None,
        last_probe_at=None,
        last_probe_result=None,
        last_success_at=None,
        created_at=now,
        updated_at=now,
    )


def _target_row(conn: GcpWifConnectionRow, name: str = "vm-1") -> GcpWifTargetRow:
    return GcpWifTargetRow(
        target_id=uuid.uuid4(),
        conn_id=conn.conn_id,
        org_id=conn.org_id,
        gcp_project_id="customer-prod",
        zone="us-central1-a",
        instance_name=name,
        enabled=True,
        created_at=_now(),
    )


@pytest_asyncio.fixture()
async def app_db(migrated_public: None):
    """A ``Database`` pool connected as the NON-SUPERUSER app role (RLS subject)."""
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")
    db = Database(APP_DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Migration shape
# ---------------------------------------------------------------------------

@requires_pg
@pytest.mark.parametrize("table", ["gcp_wif_connections", "gcp_wif_targets"])
async def test_migration_shape(admin_conn, pg_schema: str, table: str) -> None:
    rel = await admin_conn.fetchrow(
        "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname=$2 AND n.nspname=$1",
        pg_schema, table,
    )
    assert rel is not None, f"{table} missing"
    assert rel["relrowsecurity"] is True, "RLS not ENABLEd"
    assert rel["relforcerowsecurity"] is True, "RLS not FORCEd — owner would bypass"

    pol = await admin_conn.fetchval(
        "SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname=$2 AND n.nspname=$1 AND p.polname='tenant_isolation'",
        pg_schema, table,
    )
    assert pol == 1


@requires_pg
@pytest.mark.parametrize(
    "constraint",
    [
        "gcp_wif_connections_state_check",
        "gcp_wif_connections_access_mode_check",
        "gcp_wif_connections_sa_matches_mode_check",
        "gcp_wif_connections_jwks_delivery_check",
        "gcp_wif_connections_probe_result_check",
    ],
)
async def test_check_constraints_exist(
    admin_conn, pg_schema: str, constraint: str
) -> None:
    found = await admin_conn.fetchval(
        "SELECT count(*) FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname='gcp_wif_connections' AND n.nspname=$1 AND con.conname=$2",
        pg_schema, constraint,
    )
    assert found == 1


@requires_pg
async def test_connection_state_check_rejects_an_unknown_state(
    admin_conn, migrated_public: None
) -> None:
    org = f"wif_chk_{uuid.uuid4().hex[:8]}"
    try:
        await _seed_tenant(admin_conn, org)
        with pytest.raises(Exception) as exc:
            await admin_conn.execute(
                "INSERT INTO gcp_wif_connections (org_id, issuer_slug, "
                "gcp_project_id, gcp_project_number, workload_identity_pool_id, "
                "workload_identity_provider_id, audience, signing_key_id, "
                "connection_state) "
                "VALUES ($1,$2,'p','1','pool','prov','aud','k','bogus_state')",
                org, _slug(),
            )
        assert "gcp_wif_connections_state_check" in str(exc.value)
    finally:
        await _cleanup(admin_conn, [org])


@requires_pg
async def test_impersonation_without_a_service_account_is_refused(
    admin_conn, migrated_public: None
) -> None:
    """The biconditional CHECK: a half-configured trust cannot be stored.

    An impersonation connection with no service account has nothing to
    impersonate; a direct connection carrying one has an ignored field that a
    reader would reasonably believe is in use. Both are refused.
    """
    org = f"wif_sa_{uuid.uuid4().hex[:8]}"
    try:
        await _seed_tenant(admin_conn, org)
        base = (
            "INSERT INTO gcp_wif_connections (org_id, issuer_slug, gcp_project_id, "
            "gcp_project_number, workload_identity_pool_id, "
            "workload_identity_provider_id, audience, signing_key_id, access_mode, "
            "service_account_email) VALUES ($1,$2,'p','1','pool','prov','aud','k',"
        )
        with pytest.raises(Exception) as exc:
            await admin_conn.execute(base + "'impersonation', NULL)", org, _slug())
        assert "sa_matches_mode" in str(exc.value)

        with pytest.raises(Exception) as exc2:
            await admin_conn.execute(
                base + "'direct', 'sa@p.iam.gserviceaccount.com')", org, _slug()
            )
        assert "sa_matches_mode" in str(exc2.value)
    finally:
        await _cleanup(admin_conn, [org])


@requires_pg
async def test_issuer_slug_is_globally_unique_across_orgs(
    admin_conn, migrated_public: None
) -> None:
    """A TENANCY control, not a convenience.

    The slug is the public issuer path. Two orgs sharing one would mean each
    org's Google provider — pinned to that issuer URI — would accept the other's
    assertions. The database refuses it rather than trusting the generator.
    """
    org_a, org_b = _orgs()
    shared = _slug()
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        stmt = (
            "INSERT INTO gcp_wif_connections (org_id, issuer_slug, gcp_project_id, "
            "gcp_project_number, workload_identity_pool_id, "
            "workload_identity_provider_id, audience, signing_key_id) "
            "VALUES ($1,$2,'p','1','pool','prov','aud','k')"
        )
        await admin_conn.execute(stmt, org_a, shared)
        with pytest.raises(Exception) as exc:
            await admin_conn.execute(stmt, org_b, shared)
        assert "issuer_slug" in str(exc.value)
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


# ---------------------------------------------------------------------------
# RLS — THE HARD GATE
# ---------------------------------------------------------------------------

@requires_app_role
async def test_rls_blocks_cross_tenant_connection_access(
    app_db, admin_conn, pg_schema: str
) -> None:
    """Org A's federation trust must be invisible AND immutable from org B.

    Proven as `skylize_app`, asserted below to be neither a superuser nor the
    table owner — either would bypass RLS and make this test vacuous.

    This table names which of a customer's machines Skylize may act on, so a
    cross-tenant leak here is not an information disclosure but a potential
    cross-customer action.
    """
    org_a, org_b = _orgs()
    repo = PgGcpWifRepository(app_db)
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)

        row_a = _conn_row(org_a, state="valid")
        await repo.insert(row_a)

        assert (await repo.get(org_a, "")) is not None
        assert (await repo.get(org_b, "")) is None
        assert await repo.list_for_org(org_b) == []

        # B cannot mutate A's row even naming A's conn_id explicitly.
        changed = await repo.set_connection_state(
            conn_id=row_a.conn_id, org_id=org_b, state="revoked", reason="attack",
        )
        assert changed is False
        still = await repo.get(org_a, "")
        assert still is not None and still.connection_state == "valid", (
            "cross-tenant write must not land"
        )

        # Nor can B forge a probe result onto A's connection.
        forged = await repo.record_probe(
            conn_id=row_a.conn_id, org_id=org_b, result="ok", state="valid",
            reason="forged", probed_at=_now(),
        )
        assert forged is False

        # A raw cross-tenant SELECT under B's binding, with NO org predicate in
        # the SQL — proving the POLICY does the work, not the DAL's redundant
        # WHERE clause.
        async with app_db.tenant_session(org_b) as conn:
            leaked = await conn.fetch("SELECT conn_id FROM gcp_wif_connections")
            assert leaked == [], "RLS policy leaked federation rows across tenants"

        # The role must genuinely be an RLS subject or none of the above proves
        # anything.
        rolrow = await admin_conn.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname='skylize_app'"
        )
        assert rolrow is not None
        assert rolrow["rolsuper"] is False, "skylize_app is a superuser — bypasses RLS"
        assert rolrow["rolbypassrls"] is False, "skylize_app has BYPASSRLS"
        owner = await admin_conn.fetchval(
            "SELECT pg_get_userbyid(relowner) FROM pg_class "
            "WHERE relname='gcp_wif_connections'"
        )
        assert owner != "skylize_app", "owner bypasses RLS unless FORCEd"
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


@requires_app_role
async def test_rls_blocks_cross_tenant_target_access(
    app_db, admin_conn, pg_schema: str
) -> None:
    """The target list is the allow-list of stoppable machines. It must isolate
    exactly as strictly as the connection does — which is why `org_id` is
    denormalised onto it rather than reached through a join."""
    org_a, org_b = _orgs()
    repo = PgGcpWifRepository(app_db)
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)

        conn_a = _conn_row(org_a)
        await repo.insert(conn_a)
        await repo.add_target(_target_row(conn_a, "a-prod-vm"))

        assert len(await repo.list_targets(org_a, conn_a.conn_id)) == 1
        # B naming A's conn_id explicitly still sees nothing.
        assert await repo.list_targets(org_b, conn_a.conn_id) == []

        async with app_db.tenant_session(org_b) as conn:
            leaked = await conn.fetch(
                "SELECT instance_name FROM gcp_wif_targets"
            )
            assert leaked == [], "RLS leaked another tenant's stoppable instances"
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


# ---------------------------------------------------------------------------
# DAL round-trip
# ---------------------------------------------------------------------------

@requires_app_role
async def test_structured_columns_round_trip(app_db, admin_conn) -> None:
    org_a, _ = _orgs()
    repo = PgGcpWifRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org_a)
        row = _conn_row(org_a)
        await repo.insert(row)

        back = await repo.get(org_a, "")
        assert back is not None
        assert back.issuer_slug == row.issuer_slug
        assert back.gcp_project_number == "123456789012"
        assert back.access_mode == "direct"
        assert back.service_account_email is None
        assert back.jwks_delivery == "served"
        assert back.signing_key_id == "wif-es256-v1"
        assert back.connection_state == "unverified"
        assert back.last_probe_result is None
    finally:
        await _cleanup(admin_conn, [org_a])


@requires_app_role
async def test_probe_persistence_advances_success_only_on_ok(
    app_db, admin_conn
) -> None:
    """`last_success_at` is the freshness signal a staleness alert reads.

    A failed probe that refreshed it would make a broken federation look recently
    healthy — the precise false-negative this whole subsystem exists to prevent.
    """
    org_a, _ = _orgs()
    repo = PgGcpWifRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org_a)
        row = _conn_row(org_a)
        await repo.insert(row)

        t0 = _now()
        await repo.record_probe(
            conn_id=row.conn_id, org_id=org_a, result="ok", state="valid",
            reason=None, probed_at=t0,
        )
        healthy = await repo.get(org_a, "")
        assert healthy is not None
        assert healthy.connection_state == "valid"
        assert healthy.last_success_at is not None

        t1 = t0 + timedelta(minutes=5)
        await repo.record_probe(
            conn_id=row.conn_id, org_id=org_a, result="compute_denied",
            state="misconfigured", reason="binding removed", probed_at=t1,
        )
        broken = await repo.get(org_a, "")
        assert broken is not None
        assert broken.connection_state == "misconfigured"
        assert broken.last_probe_result == "compute_denied"
        assert broken.last_probe_at == t1
        assert broken.last_success_at == healthy.last_success_at, (
            "a failed probe advanced last_success_at"
        )
    finally:
        await _cleanup(admin_conn, [org_a])


@requires_app_role
async def test_targets_can_be_filtered_to_enabled_only(app_db, admin_conn) -> None:
    """A disabled target must not be probed or, later, acted on. Disabling is the
    reversible way to take a machine out of scope without losing the record that
    it was once in scope."""
    org_a, _ = _orgs()
    repo = PgGcpWifRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org_a)
        conn_row = _conn_row(org_a)
        await repo.insert(conn_row)
        await repo.add_target(_target_row(conn_row, "live-vm"))

        disabled = _target_row(conn_row, "retired-vm")
        await repo.add_target(
            GcpWifTargetRow(**{**disabled.__dict__, "enabled": False})
            if hasattr(disabled, "__dict__")
            else disabled
        )
        async with app_db.tenant_session(org_a) as conn:
            await conn.execute(
                "UPDATE gcp_wif_targets SET enabled=false WHERE instance_name='retired-vm'"
            )

        enabled = await repo.list_targets(org_a, conn_row.conn_id, enabled_only=True)
        every = await repo.list_targets(org_a, conn_row.conn_id, enabled_only=False)
        assert [t.instance_name for t in enabled] == ["live-vm"]
        assert len(every) == 2
    finally:
        await _cleanup(admin_conn, [org_a])
