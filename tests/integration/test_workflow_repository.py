"""PgWorkflowRepository against REAL Postgres as the RLS-subject app role.

Exercises the repository class itself (not hand-written SQL), because the things
most likely to break live in the mapping layer: dict -> JSONB encoding, the
None-vs-JSON-null distinction, and whether the tenant_isolation policy added in
migration 0010 actually binds for this table.

The repo connects via `Database`, whose `tenant_session` sets skylize.org_id —
so pointing it at the NON-SUPERUSER app DSN is what makes RLS meaningful here
(a superuser would bypass the policy even with FORCE).

Skipped unless SKYLIZE_TEST_DB_URL + SKYLIZE_TEST_APP_DB_URL are set.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from skylize.dal.connection import Database
from skylize.dal.ports import WorkflowRunStepRow
from skylize.dal.workflows import PgWorkflowRepository

from .conftest import APP_DB_URL, requires_app_role

pytestmark = pytest.mark.integration


async def _seed_tenant(conn, org: str) -> None:
    await conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _cleanup(conn, org: str) -> None:
    """Remove the seeded rows, child before the `tenants` parent.

    The FK on workflow_run_steps.org_id is NO ACTION (no FK to `tenants` in this
    schema cascades), so the step row must go first or the parent DELETE raises.
    Every caller runs this from a `finally`: these teardowns used to be trailing
    statements, so any failed assertion skipped them and leaked the org.
    """
    await conn.execute("DELETE FROM workflow_run_steps WHERE org_id=$1", org)
    await conn.execute("DELETE FROM tenants WHERE org_id=$1", org)


def _row(org: str, **overrides) -> WorkflowRunStepRow:
    now = datetime.now(timezone.utc)
    defaults = dict(
        step_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        org_id=org,
        step_name="draft_copy",
        step_order=1,
        agent_id="agent.copywriter",
        status="completed",
        input={"brief": "launch email", "tone": "warm"},
        output={"text": "Hello world", "tokens": 12},
        judge_verdict={"passed": True, "score": 0.91},
        error_message=None,
        retry_count=0,
        created_at=now,
        completed_at=now,
    )
    defaults.update(overrides)
    return WorkflowRunStepRow(**defaults)  # type: ignore[arg-type]


@pytest_asyncio.fixture()
async def app_db(migrated_public: None):
    """A `Database` bound to the non-superuser app role — the RLS-subject path."""
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")
    db = Database(APP_DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


@requires_app_role
@pytest.mark.asyncio
async def test_record_step_persists_every_field(app_db, admin_conn) -> None:
    """All 14 columns round-trip, with JSONB decoded back to dicts."""
    org = f"org_wf_{uuid.uuid4().hex[:8]}"
    await _seed_tenant(admin_conn, org)
    row = _row(org)
    try:
        await PgWorkflowRepository(app_db).record_step(row)

        rec = await admin_conn.fetchrow(
            "SELECT * FROM workflow_run_steps WHERE step_id=$1", row.step_id
        )
        assert rec is not None, "record_step did not persist the row"

        assert rec["run_id"] == row.run_id
        assert rec["org_id"] == org
        assert rec["step_name"] == "draft_copy"
        assert rec["step_order"] == 1
        assert rec["agent_id"] == "agent.copywriter"
        assert rec["status"] == "completed"
        assert rec["retry_count"] == 0
        assert rec["error_message"] is None
        assert rec["created_at"] == row.created_at
        assert rec["completed_at"] == row.completed_at

        # JSONB columns must come back as JSON objects, not the string "{...}".
        import json

        assert json.loads(rec["input"]) == {"brief": "launch email", "tone": "warm"}
        assert json.loads(rec["output"]) == {"text": "Hello world", "tokens": 12}
        assert json.loads(rec["judge_verdict"]) == {"passed": True, "score": 0.91}
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
@pytest.mark.asyncio
async def test_nullable_json_columns_are_sql_null_not_json_null(app_db, admin_conn) -> None:
    """A failed step carries no output/verdict — those must be SQL NULL.

    json.dumps(None) would store the JSON scalar `null`, which is NOT NULL in
    SQL and would silently corrupt `WHERE output IS NULL` triage queries.
    """
    org = f"org_wf_{uuid.uuid4().hex[:8]}"
    await _seed_tenant(admin_conn, org)
    row = _row(
        org,
        status="failed",
        output=None,
        judge_verdict=None,
        error_message="upstream timeout",
        retry_count=2,
        completed_at=None,
    )
    try:
        await PgWorkflowRepository(app_db).record_step(row)

        rec = await admin_conn.fetchrow(
            "SELECT output, judge_verdict, output IS NULL AS out_null, "
            "judge_verdict IS NULL AS verdict_null, error_message, retry_count, completed_at "
            "FROM workflow_run_steps WHERE step_id=$1",
            row.step_id,
        )
        assert rec["out_null"] is True, "output stored as JSON null instead of SQL NULL"
        assert rec["verdict_null"] is True, "judge_verdict stored as JSON null instead of SQL NULL"
        assert rec["error_message"] == "upstream timeout"
        assert rec["retry_count"] == 2
        assert rec["completed_at"] is None
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
@pytest.mark.asyncio
async def test_rls_isolates_run_steps_across_tenants(app_db, admin_conn) -> None:
    """Bound to org B, the app role cannot see org A's step — policy 0010 binds."""
    suffix = uuid.uuid4().hex[:8]
    org_a, org_b = f"org_a_{suffix}", f"org_b_{suffix}"
    await _seed_tenant(admin_conn, org_a)
    await _seed_tenant(admin_conn, org_b)
    try:
        row_a = _row(org_a)
        await PgWorkflowRepository(app_db).record_step(row_a)

        # tenant_session(org_b) binds skylize.org_id=org_b; RLS must hide org A's row.
        async with app_db.tenant_session(org_b) as conn:
            visible = await conn.fetchval(
                "SELECT COUNT(*) FROM workflow_run_steps WHERE step_id=$1", row_a.step_id
            )
        assert visible == 0, "RLS leak: org B can read org A's workflow_run_steps row"

        # The owning tenant still sees it (the policy isolates, it doesn't just deny).
        async with app_db.tenant_session(org_a) as conn:
            own = await conn.fetchval(
                "SELECT COUNT(*) FROM workflow_run_steps WHERE step_id=$1", row_a.step_id
            )
        assert own == 1, "owning tenant cannot read its own row"
    finally:
        await _cleanup(admin_conn, org_a)
        await _cleanup(admin_conn, org_b)
