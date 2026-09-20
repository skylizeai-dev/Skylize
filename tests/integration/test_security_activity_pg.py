"""`AuditActivitySignalDAL.read_recent_governance_events` against REAL Postgres.

Sibling to `test_activity_signal_source_pg.py`, which already proves
`read_window`'s counts and RLS boundary; this file proves the same two things
for the ROW read the console's security-posture screen uses beside those
counts:

  * the SQL selects the right rows -- only ATTENTION_RESULTS (denied, escalated,
    failed), the same half-open window as read_window, newest first, and no
    content-hash column;
  * the read is a genuine TENANT boundary, proven as the NOBYPASSRLS
    `skylize_app` role (`requires_app_role`) -- a leak here would put one org's
    governance denials on another org's security screen.

Also covers the route end-to-end against real Postgres (RBAC + the org-scoping
+ the "no score/controls/badges" contract), the way
`tests/integration/test_org_autonomy_mode_pg.py` covers its DAL and
`tests/unit/test_audit_routes.py`/`test_security_routes.py` cover the memory
backend's route wiring already.

Skipped unless SKYLIZE_TEST_DB_URL (+ SKYLIZE_TEST_APP_DB_URL) are set. The app
role must be the non-superuser, non-owner `skylize_app` role or the isolation
test proves nothing; that is asserted in the isolation test itself, not
assumed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from skylize.dal.activity_signals import AuditActivitySignalDAL
from skylize.dal.connection import Database
from skylize.edge.gateway import create_app

from .conftest import (
    APP_DB_URL,
    install_dev_header_auth,
    purge_tenants,
    requires_app_role,
)

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
                                   source_agent_id, authority_level,
                                   governance_token_id, action_type, result,
                                   result_reason, occurred_at)
            VALUES ($1, $2, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            uuid.uuid4(), org, uuid.uuid4(), "hook_generator_agent", "L1",
            uuid.uuid4(), action_type, result, f"{result} for testing", occurred_at,
        )


async def _cleanup(admin_conn, orgs: list[str]) -> None:
    """`audit_log` is append-only (BEFORE DELETE trigger); TRUNCATE is the same
    escape `test_activity_signal_source_pg.py` and `purge_tenants` document."""
    await admin_conn.execute("TRUNCATE audit_log")
    await purge_tenants(admin_conn, orgs)


@pytest.fixture()
def orgs() -> tuple[str, str]:
    stamp = uuid.uuid4().hex[:10]
    return f"secorg_a_{stamp}", f"secorg_b_{stamp}"


# ---------------------------------------------------------------------------
# DAL — row shape, window, and result filter
# ---------------------------------------------------------------------------


async def test_it_returns_only_attention_results_newest_first(app_db, admin_conn, orgs):
    org, _ = orgs
    base = NOW - timedelta(minutes=30)
    await _seed(admin_conn, org, [
        ("agent.executed", "success", base),
        ("agent.executed", "denied", base + timedelta(seconds=1)),
        ("agent.executed", "escalated", base + timedelta(seconds=2)),
        ("agent.executed", "failed", base + timedelta(seconds=3)),
    ])
    try:
        rows = await AuditActivitySignalDAL(app_db).read_recent_governance_events(
            org_id=org, since=NOW - timedelta(hours=1), until=NOW
        )
        assert [r.result for r in rows] == ["failed", "escalated", "denied"], (
            "success excluded; newest first"
        )
        assert all(r.occurred_at.tzinfo is not None for r in rows)
    finally:
        await _cleanup(admin_conn, [org])


async def test_the_window_is_half_open_matching_read_window(app_db, admin_conn, orgs):
    org, _ = orgs
    boundary = NOW - timedelta(hours=1)
    await _seed(admin_conn, org, [
        ("agent.executed", "denied", boundary),                        # == since
        ("agent.executed", "denied", boundary - timedelta(seconds=1)),  # before
        ("agent.executed", "denied", NOW),                              # == until
    ])
    try:
        dal = AuditActivitySignalDAL(app_db)
        this_hour = await dal.read_recent_governance_events(
            org_id=org, since=boundary, until=NOW
        )
        prev_hour = await dal.read_recent_governance_events(
            org_id=org, since=boundary - timedelta(hours=1), until=boundary
        )
        assert len(this_hour) == 1, "the `since` boundary row belongs to this window"
        assert len(prev_hour) == 1, "and the earlier row to the previous one"
    finally:
        await _cleanup(admin_conn, [org])


async def test_limit_bounds_the_page_and_counts_still_see_the_rest(
    app_db, admin_conn, orgs
):
    org, _ = orgs
    inside = NOW - timedelta(minutes=5)
    await _seed(admin_conn, org, [
        ("agent.executed", "denied", inside + timedelta(seconds=i)) for i in range(5)
    ])
    try:
        dal = AuditActivitySignalDAL(app_db)
        rows = await dal.read_recent_governance_events(
            org_id=org, since=NOW - timedelta(hours=1), until=NOW, limit=2
        )
        counts = await dal.read_window(org_id=org, since=NOW - timedelta(hours=1), until=NOW)
        assert len(rows) == 2
        assert counts.by_result["denied"] == 5, (
            "the limited feed must not be mistaken for the whole count"
        )
    finally:
        await _cleanup(admin_conn, [org])


async def test_a_quiet_window_is_an_empty_list_not_an_error(app_db, admin_conn, orgs):
    org, _ = orgs
    await _seed(admin_conn, org, [])
    try:
        rows = await AuditActivitySignalDAL(app_db).read_recent_governance_events(
            org_id=org, since=NOW - timedelta(hours=1), until=NOW
        )
        assert rows == []
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# RLS — the isolation only a real database proves
# ---------------------------------------------------------------------------


async def test_one_orgs_events_are_invisible_to_another_org(
    app_db, admin_conn, app_conn, orgs
):
    """The whole reason this runs on real Postgres: a leak here would put org
    B's governance denials on org A's security screen."""
    org_a, org_b = orgs
    inside = NOW - timedelta(minutes=10)
    await _seed(admin_conn, org_a, [("agent.executed", "denied", inside)])
    await _seed(admin_conn, org_b, [("agent.executed", "denied", inside)] * 4)
    try:
        rows = await AuditActivitySignalDAL(app_db).read_recent_governance_events(
            org_id=org_a, since=NOW - timedelta(hours=1), until=NOW
        )
        assert len(rows) == 1, "org A must not see org B's 4 rows"

        # Prove the connecting role is neither superuser nor BYPASSRLS, the same
        # assertion test_org_autonomy_mode_pg.py makes for its own table --
        # otherwise this isolation result would be a WHERE clause working by
        # coincidence rather than RLS actually applying.
        role = await app_conn.fetchval("SELECT current_user")
        rolrow = await admin_conn.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=$1", role
        )
        assert rolrow["rolsuper"] is False, f"{role} is a superuser — would bypass RLS"
        assert rolrow["rolbypassrls"] is False, f"{role} has BYPASSRLS — would bypass RLS"
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


# ---------------------------------------------------------------------------
# Route, end to end
# ---------------------------------------------------------------------------


async def test_route_reports_real_counts_and_no_score_controls_or_badges(
    admin_conn, orgs, app_db
):
    org, _ = orgs
    inside = NOW - timedelta(minutes=5)
    await _seed(admin_conn, org, [
        ("agent.executed", "success", inside),
        ("agent.executed", "denied", inside),
    ])
    try:
        app = create_app()
        install_dev_header_auth(app)
        with TestClient(app) as client:
            client.app.state.container.security_activity_dal = AuditActivitySignalDAL(app_db)
            resp = client.get(
                "/api/v1/security/activity",
                headers={"X-Dev-Org": org, "X-Dev-User": "u1", "X-Dev-Roles": "owner"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_actions"] == 2
        assert body["by_result"]["denied"] == 1
        assert body["by_result"]["success"] == 1
        assert len(body["recent_events"]) == 1
        assert body["recent_events"][0]["result"] == "denied"
        # The hard gate: no invented fields, on a real backend either.
        for forbidden in ("score", "controls", "compliance", "badges"):
            assert forbidden not in body
    finally:
        await _cleanup(admin_conn, [org])
