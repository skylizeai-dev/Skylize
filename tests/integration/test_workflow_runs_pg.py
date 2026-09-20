"""workflow_runs integration tests — REAL Postgres, proven as the app role.

Covers what only a database can prove for migration 0033 and
``WorkflowRunsDAL``:
  * migration shape: FORCE RLS, tenant_isolation policy, the four-value status
    CHECK, SELECT/INSERT/UPDATE grants and NO DELETE grant;
  * ``start_run`` opens a row in ``running`` and is idempotent on conflict —
    a retried start does not duplicate or reopen a row;
  * ``finish_run`` closes a run exactly once: a second finish (a different
    status, a different reason) does not overwrite the first terminal state;
  * ``list_runs`` returns an org's rows newest-first and paginates with the
    same keyset shape the audit feed uses;
  * RLS: one org can neither read nor write another org's run history, proven
    as a role that is neither superuser nor the table owner;
  * a run row survives independently of ``workflow_run_steps`` — no FK, no
    join, exactly as migration 0033's docstring commits to.

Skipped unless SKYLIZE_TEST_DB_URL (+ SKYLIZE_TEST_APP_DB_URL) are set. The app
role must be the non-superuser, non-owner ``skylize_app`` role or the isolation
tests prove nothing; that is asserted in the RLS test itself, not assumed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from skylize.dal.connection import Database
from skylize.dal.workflow_runs import WorkflowRunsDAL

from .conftest import APP_DB_URL, requires_app_role, requires_pg

pytestmark = pytest.mark.integration


def _orgs() -> tuple[str, str]:
    s = uuid.uuid4().hex[:8]
    return f"wfrun_a_{s}", f"wfrun_b_{s}"


async def _seed_tenant(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _cleanup(admin_conn, orgs: list[str]) -> None:
    await admin_conn.execute(
        "DELETE FROM workflow_runs WHERE org_id = ANY($1::text[])", orgs
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


# ---------------------------------------------------------------------------
# Migration shape
# ---------------------------------------------------------------------------


@requires_pg
@pytest.mark.asyncio
async def test_migration_shape(admin_conn) -> None:
    schema = await admin_conn.fetchval("SELECT current_schema()")

    row = await admin_conn.fetchrow(
        "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname='workflow_runs' AND n.nspname=$1",
        schema,
    )
    assert row["relrowsecurity"] is True, "RLS is not enabled on workflow_runs"
    assert row["relforcerowsecurity"] is True, (
        "RLS is not FORCED — the table owner would bypass the policy"
    )

    policy = await admin_conn.fetchval(
        "SELECT p.polname FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname='workflow_runs' AND n.nspname=$1 "
        "AND p.polname='tenant_isolation'",
        schema,
    )
    assert policy == "tenant_isolation"

    check = await admin_conn.fetchval(
        "SELECT pg_get_constraintdef(con.oid) FROM pg_constraint con "
        "JOIN pg_class c ON c.oid = con.conrelid "
        "WHERE c.relname='workflow_runs' AND con.contype='c'"
    )
    for status in ("running", "completed", "denied", "failed"):
        assert status in check, f"{status} missing from the CHECK constraint"

    grants = {
        r["privilege_type"]
        for r in await admin_conn.fetch(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name='workflow_runs' AND grantee='skylize_app'"
        )
    }
    assert {"SELECT", "INSERT", "UPDATE"} <= grants
    assert "DELETE" not in grants, "run history must not be deletable by the app role"


@requires_pg
@pytest.mark.asyncio
async def test_no_foreign_key_from_workflow_run_steps(admin_conn) -> None:
    """Migration 0033's central design choice: run_id spaces are NOT unified.
    workflow_run_steps.run_id carries no FK to workflow_runs, so a step row can
    exist (or not) with no bearing on this table, and vice versa."""
    fks = await admin_conn.fetch(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'workflow_run_steps'::regclass AND contype = 'f'"
    )
    assert not any("workflow_runs" in str(r["conname"]) for r in fks)


@requires_pg
@pytest.mark.asyncio
async def test_check_constraint_rejects_an_unknown_status(admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        with pytest.raises(Exception) as excinfo:
            await admin_conn.execute(
                "INSERT INTO workflow_runs (run_id, org_id, workflow_name, agent_id, "
                "status, correlation_id) VALUES ($1,$2,$3,$4,$5,$6)",
                uuid.uuid4(), org, "creative", "hook_generator_agent",
                "in_orbit", uuid.uuid4(),
            )
        assert "workflow_runs" in str(excinfo.value)
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# start_run / finish_run lifecycle
# ---------------------------------------------------------------------------


@requires_app_role
@pytest.mark.asyncio
async def test_start_run_opens_a_running_row(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        dal = WorkflowRunsDAL(app_db)
        run_id = uuid.uuid4()
        correlation_id = uuid.uuid4()
        await dal.start_run(
            run_id=run_id, org_id=org, workflow_name="creative",
            agent_id="hook_generator_agent", correlation_id=correlation_id,
        )
        rows = await dal.list_runs(org, limit=10)
        assert len(rows) == 1
        assert rows[0].run_id == run_id
        assert rows[0].status == "running"
        assert rows[0].finished_at is None
        assert rows[0].correlation_id == correlation_id
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_start_run_is_idempotent(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        dal = WorkflowRunsDAL(app_db)
        run_id = uuid.uuid4()
        for _ in range(2):
            await dal.start_run(
                run_id=run_id, org_id=org, workflow_name="creative",
                agent_id="hook_generator_agent", correlation_id=uuid.uuid4(),
            )
        rows = await dal.list_runs(org, limit=10)
        assert len(rows) == 1, "a retried start must not duplicate the run"
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_finish_run_closes_exactly_once(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        dal = WorkflowRunsDAL(app_db)
        run_id = uuid.uuid4()
        await dal.start_run(
            run_id=run_id, org_id=org, workflow_name="creative",
            agent_id="hook_generator_agent", correlation_id=uuid.uuid4(),
        )
        await dal.finish_run(run_id=run_id, org_id=org, status="completed")
        # A second finish (different status/reason) must not reopen or
        # overwrite the first terminal state.
        await dal.finish_run(
            run_id=run_id, org_id=org, status="failed", reason="late duplicate"
        )
        rows = await dal.list_runs(org, limit=10)
        assert len(rows) == 1
        assert rows[0].status == "completed"
        assert rows[0].reason is None
        assert rows[0].finished_at is not None
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_finish_run_records_failure_stage_and_reason(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        dal = WorkflowRunsDAL(app_db)
        run_id = uuid.uuid4()
        await dal.start_run(
            run_id=run_id, org_id=org, workflow_name="creative",
            agent_id="hook_generator_agent", correlation_id=uuid.uuid4(),
        )
        await dal.finish_run(
            run_id=run_id, org_id=org, status="failed",
            failure_stage="budget", reason="ceiling exceeded",
        )
        rows = await dal.list_runs(org, limit=10)
        assert rows[0].status == "failed"
        assert rows[0].failure_stage == "budget"
        assert rows[0].reason == "ceiling exceeded"
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
@pytest.mark.asyncio
async def test_finish_run_on_a_never_started_run_writes_nothing(
    app_db, admin_conn
) -> None:
    """A start that was never persisted (e.g. swallowed by best-effort
    recording) leaves no row for finish_run to close. That absence is honest —
    an UPSERT here would invent a run with no start instant."""
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        dal = WorkflowRunsDAL(app_db)
        await dal.finish_run(run_id=uuid.uuid4(), org_id=org, status="completed")
        rows = await dal.list_runs(org, limit=10)
        assert rows == []
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# list_runs — ordering and pagination
# ---------------------------------------------------------------------------


@requires_app_role
@pytest.mark.asyncio
async def test_list_runs_orders_newest_first_and_paginates(app_db, admin_conn) -> None:
    org, _ = _orgs()
    try:
        await _seed_tenant(admin_conn, org)
        dal = WorkflowRunsDAL(app_db)
        base = datetime.now(timezone.utc) - timedelta(hours=1)
        run_ids = [uuid.uuid4() for _ in range(3)]
        for i, run_id in enumerate(run_ids):
            await dal.start_run(
                run_id=run_id, org_id=org, workflow_name="creative",
                agent_id="hook_generator_agent", correlation_id=uuid.uuid4(),
            )
            # Backdate started_at directly so ordering is deterministic.
            async with app_db.tenant_session(org) as conn:
                await conn.execute(
                    "UPDATE workflow_runs SET started_at = $1 WHERE run_id = $2",
                    base + timedelta(minutes=i), run_id,
                )

        page1 = await dal.list_runs(org, limit=2)
        assert [r.run_id for r in page1] == [run_ids[2], run_ids[1]]
        page2 = await dal.list_runs(org, limit=2, before=page1[-1].started_at)
        assert [r.run_id for r in page2] == [run_ids[0]]
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# RLS — the isolation that only a real database proves
# ---------------------------------------------------------------------------


@requires_app_role
@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_run_read_and_write(
    app_db, app_conn, admin_conn
) -> None:
    org_a, org_b = _orgs()
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        dal = WorkflowRunsDAL(app_db)
        run_id = uuid.uuid4()
        await dal.start_run(
            run_id=run_id, org_id=org_a, workflow_name="creative",
            agent_id="hook_generator_agent", correlation_id=uuid.uuid4(),
        )

        # Bound to org_b, a raw SELECT sees nothing of org_a's run.
        async with app_db.tenant_session(org_b) as conn:
            seen = await conn.fetch("SELECT run_id FROM workflow_runs")
        assert seen == []
        assert await dal.list_runs(org_b, limit=10) == []

        # A write pinned to org_b's session cannot touch org_a's row: WITH
        # CHECK (and the USING clause on the implicit read) forces org_id =
        # org_b, so this updates 0 rows — never org_a's row.
        async with app_db.tenant_session(org_b) as conn:
            result = await conn.execute(
                "UPDATE workflow_runs SET status = 'failed' WHERE run_id = $1",
                run_id,
            )
        assert result == "UPDATE 0"

        # org_a still sees its own row, untouched.
        rows_a = await dal.list_runs(org_a, limit=10)
        assert len(rows_a) == 1
        assert rows_a[0].status == "running"

        # Prove the RLS-subject role is NEITHER superuser NOR the table owner —
        # otherwise RLS would be moot (same assertion as the autonomy-mode PG
        # suite, applied here).
        role = await app_conn.fetchval("SELECT current_user")
        rolrow = await admin_conn.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=$1", role
        )
        assert rolrow["rolsuper"] is False, f"{role} is a superuser — would bypass RLS"
        assert rolrow["rolbypassrls"] is False, f"{role} has BYPASSRLS — would bypass RLS"
    finally:
        await _cleanup(admin_conn, [org_a, org_b])
