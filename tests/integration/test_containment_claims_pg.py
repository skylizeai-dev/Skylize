"""The durable, cross-replica containment claim - proven against REAL Postgres.

Unit tests (test_containment_autohook.py, test_gcp_wif_gate_and_trigger.py) prove
the trigger's mechanics against `InMemoryGcpContainmentClaimRepository`, which is
DELIBERATELY simpler than the Pg implementation (no `hitl_queue` join, crash
timeout only - see that class's docstring). This file proves the two properties
only a real database can prove:

  * THE RACE IS ACTUALLY CLOSED. Two independent trigger instances - the closest
    a single-process test suite can get to two application replicas - racing the
    SAME breach for the SAME org resolve to exactly one winner, because Postgres
    itself arbitrates the conflicting `INSERT` on the shared row.
  * THE CLAIM IS GENUINELY HITL-QUEUE-BACKED. Once a human decides the pending
    ticket a claim guards, the very next breach may claim again immediately - it
    does not wait out the crash-recovery timeout, because staleness is checked by
    joining against the real `hitl_queue.status`, not a stored expiry.

Also re-verified here, at the level the durable claim itself operates, rather
than through the full auto-hook path: a breach that never reaches the claim step
(no federation, broken trust, no targets) can never block a later one, because no
row is ever written for it.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from skylize.app.agents.execution import AgentDeferredToHuman
from skylize.app.gcp.trigger import SpendCeilingContainmentTrigger
from skylize.dal.connection import Database
from skylize.dal.gcp_containment import PgGcpContainmentClaimRepository
from skylize.dal.gcp_wif import (
    GcpWifConnectionRow,
    GcpWifTargetRow,
    PgGcpWifRepository,
)

from .conftest import APP_DB_URL, requires_app_role, requires_pg

pytestmark = pytest.mark.integration

PROJECT, ZONE, INSTANCE = "cust-prod", "us-central1-a", "runaway"


@pytest_asyncio.fixture()
async def app_db(migrated_public: None) -> AsyncIterator[Database]:
    """A `Database` pool connected as the NON-SUPERUSER app role (RLS subject)."""
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")
    db = Database(APP_DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


def _org() -> str:
    return f"claim_{uuid.uuid4().hex[:10]}"


async def _seed_tenant(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _seed_federation(app_db: Database, org: str) -> GcpWifConnectionRow:
    now = datetime.now(timezone.utc)
    row = GcpWifConnectionRow(
        conn_id=uuid.uuid4(), org_id=org, label="",
        issuer_slug=uuid.uuid4().hex[:26].ljust(26, "z"),
        gcp_project_id=PROJECT, gcp_project_number="123456789012",
        workload_identity_pool_id="pool", workload_identity_provider_id="prov",
        audience="//iam.googleapis.com/projects/1/locations/global/x/y",
        access_mode="direct", service_account_email=None,
        signing_key_id="wif-es256-v1", jwks_delivery="served",
        expected_jwks_key_id=None, connection_state="valid", state_reason=None,
        last_probe_at=None, last_probe_result=None, last_success_at=None,
        created_at=now, updated_at=now,
    )
    repo = PgGcpWifRepository(app_db)
    await repo.insert(row)
    await repo.add_target(GcpWifTargetRow(
        target_id=uuid.uuid4(), conn_id=row.conn_id, org_id=org,
        gcp_project_id=PROJECT, zone=ZONE, instance_name=INSTANCE,
        enabled=True, created_at=now,
    ))
    return row


async def _cleanup(admin_conn, orgs: list[str]) -> None:
    for table in ("gcp_containment_claims", "hitl_queue", "decisions",
                  "gcp_wif_targets", "gcp_wif_connections"):
        await admin_conn.execute(
            f"DELETE FROM {table} WHERE org_id = ANY($1::text[])", orgs
        )


class _FakeExecution:
    """A minimal `execute()` double that always defers, recording every call.

    Used in this file wherever the property under test is the CLAIM's own
    atomicity or staleness logic, not the full agent-execution/decision-gate
    machinery - that full path is already exercised end to end in
    test_containment_autohook_pg.py and test_gcp_killswitch_e2e_pg.py.

    WRITES A REAL `hitl_queue` ROW BEFORE RAISING - THIS IS LOAD-BEARING, NOT
    DECORATION. An earlier version of this double raised `AgentDeferredToHuman`
    with a freshly-minted `hitl_id` that named NO real row anywhere. That is
    fine for tests that never re-claim after the fact, but
    `test_two_replicas_racing_the_same_breach_only_one_wins` (below) races TWO
    of these against the SAME org, and the durable claim's staleness check asks
    "does `hitl_queue` still show this ticket pending?" (dal/gcp_containment.py).
    A ticket that was never written answers that question "no" immediately -
    the claim looks resolved the instant it is taken, and a second racer that
    reaches `try_claim` even a moment later legitimately (and correctly, given
    what it can see) steals it. That produced an intermittent double-`proposed`
    result roughly one run in five: not a flaw in the atomic claim - proven
    unconditionally by `test_ten_concurrent_breaches_produce_exactly_one_winner`,
    which calls `try_claim` directly and has never shown the same failure - but
    a fake that under-simulated what a real deferred proposal always leaves
    behind. Each instance writes through its OWN `Database` pool, never a
    connection shared with another instance, so two racing fakes cannot
    collide on the write itself.
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        self.calls = 0
        self.last_hitl_id: uuid.UUID | None = None

    async def execute(self, *, org_id: str, **_kw):
        self.calls += 1
        hitl_id = uuid.uuid4()
        self.last_hitl_id = hitl_id
        await _insert_real_hitl_row_via(self._db, org=org_id, hitl_id=hitl_id)
        raise AgentDeferredToHuman(hitl_id=hitl_id, reason="external_publication")


async def _insert_real_hitl_row(
    admin_conn, *, org: str, hitl_id: uuid.UUID, status: str = "pending"
) -> None:
    """Write a minimal, genuinely valid `hitl_queue` row naming `hitl_id`.

    Needed so the Pg claim repository's staleness JOIN has a real row to
    evaluate `status` against - the property under test is that JOIN, so it must
    run against the real table, not a stand-in.
    """
    now = datetime.now(timezone.utc)
    await admin_conn.execute(
        """
        INSERT INTO hitl_queue (
            hitl_id, org_id, correlation_id, partition_key, trigger_reason,
            proposal_json, status, expires_at, created_at
        ) VALUES ($1, $2, $3, $4, 'external_publication', '{}'::jsonb, $5,
                  $6, $7)
        """,
        hitl_id, org, uuid.uuid4(), f"agent_execute:infrastructure_executor:{hitl_id}",
        status, now + timedelta(hours=48), now,
    )


async def _insert_real_hitl_row_via(
    db: Database, *, org: str, hitl_id: uuid.UUID, status: str = "pending"
) -> None:
    """The same write as `_insert_real_hitl_row`, through a tenant-scoped
    `Database` connection instead of the shared admin connection.

    Used by `_FakeExecution` so two racing instances - each on its OWN pool -
    never contend for the SAME raw connection object, which a single shared
    `admin_conn` cannot safely serve to two concurrently-running coroutines.
    """
    now = datetime.now(timezone.utc)
    async with db.tenant_session(org) as conn:
        await conn.execute(
            """
            INSERT INTO hitl_queue (
                hitl_id, org_id, correlation_id, partition_key, trigger_reason,
                proposal_json, status, expires_at, created_at
            ) VALUES ($1, $2, $3, $4, 'external_publication', '{}'::jsonb, $5,
                      $6, $7)
            """,
            hitl_id, org, uuid.uuid4(),
            f"agent_execute:infrastructure_executor:{hitl_id}",
            status, now + timedelta(hours=48), now,
        )


@pytest_asyncio.fixture()
async def app_db_factory(migrated_public: None):
    """Yields a callable producing fresh `Database` handles on the app role.

    Two independently-constructed `Database`/pool objects, each opened and
    closed for its own scope, is the closest a single test process gets to two
    application replicas: they share NOTHING client-side, only the Postgres
    server underneath.
    """
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")

    opened: list[Database] = []

    async def _make() -> Database:
        db = Database(APP_DB_URL)
        await db.connect()
        opened.append(db)
        return db

    try:
        yield _make
    finally:
        for db in opened:
            await db.close()


# ---------------------------------------------------------------------------
# Migration shape
# ---------------------------------------------------------------------------

@requires_pg
async def test_migration_shape(admin_conn, pg_schema: str) -> None:
    rel = await admin_conn.fetchrow(
        "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname='gcp_containment_claims' AND n.nspname=$1",
        pg_schema,
    )
    assert rel is not None, "gcp_containment_claims missing"
    assert rel["relrowsecurity"] is True, "RLS not ENABLEd"
    assert rel["relforcerowsecurity"] is True, "RLS not FORCEd - owner would bypass"

    pol = await admin_conn.fetchval(
        "SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname='gcp_containment_claims' AND n.nspname=$1 "
        "AND p.polname='tenant_isolation'",
        pg_schema,
    )
    assert pol == 1

    pk = await admin_conn.fetchval(
        "SELECT count(*) FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname='gcp_containment_claims' AND n.nspname=$1 "
        "AND con.contype='p'",
        pg_schema,
    )
    assert pk == 1, "no PRIMARY KEY on (org_id, label) - the race guarantee has no anchor"


# ---------------------------------------------------------------------------
# Repository round trip, against the real table
# ---------------------------------------------------------------------------

@requires_app_role
async def test_claim_record_and_release_round_trip(admin_conn, app_db) -> None:
    org = _org()
    try:
        await _seed_tenant(admin_conn, org)
        claims = PgGcpContainmentClaimRepository(app_db)
        stale_before = datetime.now(timezone.utc) - timedelta(minutes=2)

        assert await claims.try_claim(
            org_id=org, label="", stale_before=stale_before) is True
        # Immediately re-claiming (still hitl_id=NULL, not stale) must refuse.
        assert await claims.try_claim(
            org_id=org, label="", stale_before=stale_before) is False

        hitl_id = uuid.uuid4()
        await _insert_real_hitl_row(admin_conn, org=org, hitl_id=hitl_id)
        await claims.record_hitl_id(org_id=org, label="", hitl_id=hitl_id)

        # Still pending in hitl_queue -> still claimed.
        assert await claims.try_claim(
            org_id=org, label="", stale_before=stale_before) is False

        await claims.release(org_id=org, label="")
        # Released outright -> immediately reclaimable.
        assert await claims.try_claim(
            org_id=org, label="", stale_before=stale_before) is True
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_a_claim_never_reached_because_wif_checks_fail_blocks_nothing(
    admin_conn, app_db
) -> None:
    """The trigger runs its WIF checks BEFORE attempting the claim.

    Proven directly here at the repository level: an org for which
    `propose_containment` never called `try_claim` at all (no federation) has NO
    row, so a REAL later claim attempt for that org succeeds immediately -
    exactly the "operator connects federation, next breach proceeds" property,
    isolated from the rest of the trigger's WIF logic (already covered by
    `test_a_failed_proposal_does_not_start_the_cooldown` and
    `test_no_federation_means_a_breach_queues_nothing`).
    """
    org = _org()
    try:
        await _seed_tenant(admin_conn, org)
        claims = PgGcpContainmentClaimRepository(app_db)

        # No _seed_federation, no try_claim call - simulating the trigger's
        # early "no_connection" return, which never touches this table.
        row_exists = await app_db_row_exists_gcp_containment_claims(app_db, org)
        assert row_exists is False

        # A later, legitimate breach must claim cleanly, with no leftover state
        # from the earlier failed attempt to contend with.
        stale_before = datetime.now(timezone.utc) - timedelta(minutes=2)
        assert await claims.try_claim(
            org_id=org, label="", stale_before=stale_before) is True
    finally:
        await _cleanup(admin_conn, [org])


async def app_db_row_exists_gcp_containment_claims(app_db: Database, org: str) -> bool:
    async with app_db.tenant_session(org) as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM gcp_containment_claims WHERE org_id=$1", org
        )
        return row is not None


# ---------------------------------------------------------------------------
# RLS - cross-tenant isolation
# ---------------------------------------------------------------------------

@requires_app_role
async def test_rls_blocks_cross_tenant_claim_visibility(admin_conn, app_db) -> None:
    org_a, org_b = _org(), _org()
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)

        claims = PgGcpContainmentClaimRepository(app_db)
        stale_before = datetime.now(timezone.utc) - timedelta(minutes=2)
        assert await claims.try_claim(
            org_id=org_a, label="", stale_before=stale_before) is True

        # A raw cross-tenant SELECT under B's binding, with NO org predicate in
        # the SQL - proving the POLICY does the work, not an app-level filter.
        async with app_db.tenant_session(org_b) as conn:
            leaked = await conn.fetch("SELECT org_id FROM gcp_containment_claims")
            assert leaked == [], "RLS policy leaked another tenant's claim"

        # And B is free to claim its OWN (org_id, label) - org A's row must not
        # be a false conflict.
        assert await claims.try_claim(
            org_id=org_b, label="", stale_before=stale_before) is True

        rolrow = await admin_conn.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname='skylize_app'"
        )
        assert rolrow["rolsuper"] is False, "skylize_app is a superuser - bypasses RLS"
        assert rolrow["rolbypassrls"] is False, "skylize_app has BYPASSRLS"
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


# ---------------------------------------------------------------------------
# THE HARD REQUIREMENT: the cross-replica race is actually closed
# ---------------------------------------------------------------------------

@requires_app_role
async def test_two_replicas_racing_the_same_breach_only_one_wins(
    admin_conn, app_db_factory
) -> None:
    """Simulates two application replicas: two INDEPENDENT trigger instances,
    each on its OWN `Database` pool, sharing nothing client-side but the live
    Postgres server underneath - then races them for the SAME org via
    `asyncio.gather`.

    If the guarantee were a Python-level SELECT-then-INSERT, running two
    unrelated objects concurrently against real connections would expose the
    window. It does not, because the arbitration happens inside Postgres on the
    PRIMARY KEY `(org_id, label)` - see dal/gcp_containment.py's `_TRY_CLAIM_SQL`
    and migration 0025.
    """
    org = _org()
    try:
        await _seed_tenant(admin_conn, org)

        db_a = await app_db_factory()
        db_b = await app_db_factory()
        await _seed_federation(db_a, org)

        exec_a, exec_b = _FakeExecution(db_a), _FakeExecution(db_b)
        replica_a = SpendCeilingContainmentTrigger(
            wif_repo=PgGcpWifRepository(db_a), execution=exec_a,
            claims=PgGcpContainmentClaimRepository(db_a),
        )
        replica_b = SpendCeilingContainmentTrigger(
            wif_repo=PgGcpWifRepository(db_b), execution=exec_b,
            claims=PgGcpContainmentClaimRepository(db_b),
        )

        results = await asyncio.gather(
            replica_a.propose_containment(
                org_id=org, breach_reason="race", user_id="u1"),
            replica_b.propose_containment(
                org_id=org, breach_reason="race", user_id="u1"),
        )

        statuses = sorted(r.status for r in results)
        assert statuses == ["already_proposed", "proposed"], (
            f"expected exactly one winner and one refusal, got "
            f"{[r.status for r in results]}"
        )
        assert exec_a.calls + exec_b.calls == 1, (
            "both replicas called execute() - the database did not serialize "
            "the claim"
        )

        # Exactly one row exists for the org - not two.
        async with db_a.tenant_session(org) as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM gcp_containment_claims WHERE org_id=$1", org
            )
        assert count == 1
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_ten_concurrent_breaches_produce_exactly_one_winner(
    admin_conn, app_db
) -> None:
    """The same property under higher contention: ten concurrent attempts on
    ONE connection pool, all racing the same (org_id, label)."""
    org = _org()
    try:
        await _seed_tenant(admin_conn, org)
        claims = PgGcpContainmentClaimRepository(app_db)
        stale_before = datetime.now(timezone.utc) - timedelta(minutes=2)

        results = await asyncio.gather(*[
            claims.try_claim(org_id=org, label="", stale_before=stale_before)
            for _ in range(10)
        ])
        assert sum(1 for r in results if r) == 1, (
            f"expected exactly one winner among 10 racers, got {sum(results)}"
        )
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# THE HITL-QUEUE-BACKED HALF: early release on a human decision
# ---------------------------------------------------------------------------

@requires_app_role
async def test_claim_releases_early_once_the_human_decides(
    admin_conn, app_db
) -> None:
    """The flagship correctness improvement over a fixed timer.

    A claim whose ticket has already left `'pending'` (approved, rejected,
    expired - any non-pending status) must be reclaimable IMMEDIATELY, not after
    waiting out the crash-recovery backstop. Proven directly against a REAL
    `hitl_queue` row, which is the only place this behaviour can be proven: the
    in-memory repository does not implement this join at all.
    """
    org = _org()
    try:
        await _seed_tenant(admin_conn, org)
        claims = PgGcpContainmentClaimRepository(app_db)
        # A crash-backstop window that would NOT have elapsed - if the early
        # release depended on the timeout, this claim would still be refused.
        stale_before = datetime.now(timezone.utc) - timedelta(hours=1)

        assert await claims.try_claim(
            org_id=org, label="", stale_before=stale_before) is True
        hitl_id = uuid.uuid4()
        await _insert_real_hitl_row(admin_conn, org=org, hitl_id=hitl_id)
        await claims.record_hitl_id(org_id=org, label="", hitl_id=hitl_id)

        # Still pending -> still blocked, even against a generous stale_before.
        assert await claims.try_claim(
            org_id=org, label="", stale_before=stale_before) is False

        # A human decides.
        await admin_conn.execute(
            "UPDATE hitl_queue SET status='approved' WHERE hitl_id=$1", hitl_id
        )

        # Immediately reclaimable - the timeout window has NOT elapsed, only the
        # ticket's status changed.
        assert await claims.try_claim(
            org_id=org, label="", stale_before=stale_before) is True
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.parametrize("status", ["approved", "rejected", "expired"])
async def test_every_terminal_hitl_status_releases_the_claim(
    admin_conn, app_db, status: str
) -> None:
    org = _org()
    try:
        await _seed_tenant(admin_conn, org)
        claims = PgGcpContainmentClaimRepository(app_db)
        stale_before = datetime.now(timezone.utc) - timedelta(hours=1)

        await claims.try_claim(org_id=org, label="", stale_before=stale_before)
        hitl_id = uuid.uuid4()
        await _insert_real_hitl_row(admin_conn, org=org, hitl_id=hitl_id)
        await claims.record_hitl_id(org_id=org, label="", hitl_id=hitl_id)

        await admin_conn.execute(
            "UPDATE hitl_queue SET status=$2 WHERE hitl_id=$1", hitl_id, status
        )
        assert await claims.try_claim(
            org_id=org, label="", stale_before=stale_before) is True, (
            f"a claim whose ticket is {status!r} must be immediately reclaimable"
        )
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# The crash-recovery backstop
# ---------------------------------------------------------------------------

@requires_app_role
async def test_a_claim_with_no_ticket_is_reclaimable_only_after_the_backstop(
    admin_conn, app_db
) -> None:
    """A claimant that died before ever recording a `hitl_id` must not wedge the
    org forever - but must also not be stealable by a merely-concurrent, still
    in-flight claimant. The distinction is `stale_before`, supplied by the
    caller, never guessed by this table.
    """
    org = _org()
    try:
        await _seed_tenant(admin_conn, org)
        claims = PgGcpContainmentClaimRepository(app_db)

        assert await claims.try_claim(
            org_id=org, label="",
            stale_before=datetime.now(timezone.utc) - timedelta(minutes=2),
        ) is True
        # hitl_id is still NULL (never recorded - simulating a crash).

        # A generous stale_before (i.e. "only steal if VERY old") must still
        # refuse - the claim is recent.
        assert await claims.try_claim(
            org_id=org, label="",
            stale_before=datetime.now(timezone.utc) - timedelta(hours=1),
        ) is False

        # A stale_before in the FUTURE simulates enough wall-clock time having
        # passed for the crash backstop to apply.
        assert await claims.try_claim(
            org_id=org, label="",
            stale_before=datetime.now(timezone.utc) + timedelta(seconds=1),
        ) is True
    finally:
        await _cleanup(admin_conn, [org])
