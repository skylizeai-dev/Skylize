"""`AuditActivitySignalDAL` against REAL Postgres.

WHAT ONLY THIS CAN PROVE. Two things a fake repository cannot:

  * the SQL counts the right rows -- the window is half-open, the `FILTER`
    clauses land on the right `result` values, and an org's sweep counts only
    its own actions;
  * the read is a genuine TENANT boundary. It runs as the NOBYPASSRLS
    `skylize_app` role, so the `tenant_isolation` policy on `audit_log` is what
    excludes the other org's rows -- not a WHERE clause a refactor could drop.

The second is the one worth the infra: a signal source that leaked another
tenant's audit counts would put one org's security posture into another org's
governance record.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from skylize.dal.activity_signals import AuditActivitySignalDAL
from skylize.dal.connection import Database

from .conftest import APP_DB_URL, purge_tenants, requires_app_role

pytestmark = [pytest.mark.integration, requires_app_role]

NOW = datetime.now(timezone.utc).replace(microsecond=0)


@pytest_asyncio.fixture()
async def app_db(migrated_public: None):
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")
    db = Database(APP_DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


async def _seed(admin_conn, org: str, rows: list[tuple[str, str, datetime]]) -> None:
    """Insert tenant + audit rows as the admin role (the app role cannot create
    another org's tenant, which is the point of the isolation test below)."""
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer, status) "
        "VALUES ($1, $1, $2, 'active') ON CONFLICT (org_id) DO NOTHING",
        org,
        f"https://issuer.invalid/{org}",
    )
    for action_type, result, occurred_at in rows:
        await admin_conn.execute(
            """
            INSERT INTO audit_log (event_id, org_id, tenant_id, correlation_id,
                                   source_agent_id, action_type, result, occurred_at)
            VALUES ($1, $2, $2, $3, $4, $5, $6, $7)
            """,
            uuid.uuid4(), org, uuid.uuid4(), "fraud_detection_agent",
            action_type, result, occurred_at,
        )


async def _cleanup(admin_conn, orgs: list[str]) -> None:
    """`audit_log` is APPEND-ONLY -- a BEFORE DELETE trigger refuses a per-org
    delete, so the tenant delete would then fail on the FK. TRUNCATE does not
    fire row triggers, which is the same escape `purge_tenants` documents for
    `ai_cost_ledger` / `work_journal`. It clears every org's rows, which is
    acceptable in a disposable test database and nowhere else."""
    await admin_conn.execute("TRUNCATE audit_log")
    await purge_tenants(admin_conn, orgs)


@pytest.fixture()
def orgs() -> tuple[str, str]:
    stamp = uuid.uuid4().hex[:10]
    return f"sigorg_a_{stamp}", f"sigorg_b_{stamp}"


async def test_it_counts_the_orgs_own_window_by_result(app_db, admin_conn, orgs):
    org, _ = orgs
    inside = NOW - timedelta(minutes=30)
    await _seed(admin_conn, org, [
        ("tool.invoked", "success", inside),
        ("tool.invoked", "denied", inside),
        ("tool.invoked", "denied", inside),
        ("hitl.approve_failed", "failed", inside),
        ("decision.deferred_to_human", "escalated", inside),
    ])
    try:
        counts = await AuditActivitySignalDAL(app_db).read_window(
            org_id=org, since=NOW - timedelta(hours=1), until=NOW
        )
        assert counts.total == 5
        assert counts.by_result == {
            "success": 1, "denied": 2, "escalated": 1, "failed": 1
        }
        assert counts.distinct_action_types == 3
        assert counts.distinct_agents == 1
    finally:
        await _cleanup(admin_conn, [org])


async def test_the_window_is_half_open_so_sweeps_never_double_count(
    app_db, admin_conn, orgs
):
    """An action exactly on the boundary belongs to exactly one of two adjacent
    hourly sweeps -- the `>= since AND < until` the DAL documents."""
    org, _ = orgs
    boundary = NOW - timedelta(hours=1)
    await _seed(admin_conn, org, [
        ("tool.invoked", "denied", boundary),                        # == since
        ("tool.invoked", "denied", boundary - timedelta(seconds=1)),  # before
        ("tool.invoked", "denied", NOW),                              # == until
    ])
    try:
        dal = AuditActivitySignalDAL(app_db)
        this_hour = await dal.read_window(org_id=org, since=boundary, until=NOW)
        prev_hour = await dal.read_window(
            org_id=org, since=boundary - timedelta(hours=1), until=boundary
        )
        assert this_hour.total == 1, "the `since` boundary row belongs to this window"
        assert prev_hour.total == 1, "and the earlier row to the previous one"
    finally:
        await _cleanup(admin_conn, [org])


async def test_one_orgs_sweep_cannot_count_another_orgs_actions(
    app_db, admin_conn, orgs
):
    """RLS, as the NOBYPASSRLS app role. The whole reason this runs on real
    Postgres: a leak here would put org B's security posture on org A's record."""
    org_a, org_b = orgs
    inside = NOW - timedelta(minutes=10)
    await _seed(admin_conn, org_a, [("tool.invoked", "denied", inside)])
    await _seed(admin_conn, org_b, [("tool.invoked", "denied", inside)] * 7)
    try:
        counts = await AuditActivitySignalDAL(app_db).read_window(
            org_id=org_a, since=NOW - timedelta(hours=1), until=NOW
        )
        assert counts.total == 1, "org A must not see org B's 7 rows"
        assert counts.by_result["denied"] == 1
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


async def test_a_quiet_window_is_zeroes_not_an_error(app_db, admin_conn, orgs):
    org, _ = orgs
    await _seed(admin_conn, org, [])
    try:
        counts = await AuditActivitySignalDAL(app_db).read_window(
            org_id=org, since=NOW - timedelta(hours=1), until=NOW
        )
        assert counts.total == 0
        assert counts.by_result == {
            "success": 0, "denied": 0, "escalated": 0, "failed": 0
        }
    finally:
        await _cleanup(admin_conn, [org])
