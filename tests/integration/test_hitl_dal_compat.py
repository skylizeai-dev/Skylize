"""K3 schema-compatibility: the request-path HITL DAL vs the async writer.

A hitl_queue row written by the app-layer PgHitlQueueRepository (the synchronous
request path) must be schema-compatible with one written by the async
HITLQueueWriter (decision_engine/hitl_writer.py), so Day 2's approve endpoint can
read either: same columns populated, same status value, same column types.

Real Postgres; skipped unless SKYLIZE_TEST_DB_URL (+ APP_DB_URL) are set.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from skylize.dal.connection import Database
from skylize.dal.hitl import PgHitlQueueRepository
from skylize.dal.ports import HitlEscalation

# The other writer + its bus-side models (a TEST may import decision_engine; only
# the live request path may not — owner decision K3).
from skylize.decision_engine.config import DecisionEngineSettings
from skylize.decision_engine.hitl_writer import HITLQueueWriter
from skylize.decision_engine.models import DecisionContext, DecisionOutcome
from skylize.decision_engine.models import DecisionResult as BusDecisionResult

from .conftest import APP_DB_URL, requires_app_role

pytestmark = pytest.mark.integration

_POPULATED = {
    "hitl_id", "org_id", "decision_id", "correlation_id", "partition_key",
    "trigger_reason", "proposal_json", "status", "expires_at", "created_at",
}
_NULL_UNTIL_VERDICT = {"score_json", "verdict_by", "verdict_json", "verdict_at"}


def _org() -> str:
    return f"hitl_{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture()
async def app_db(migrated_public: None) -> AsyncIterator[Database]:
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")
    db = Database(APP_DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


async def _seed_tenant(admin_conn: object, org: str) -> None:
    await admin_conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _seed_decision(admin_conn: object, org: str, decision_id: uuid.UUID, corr: uuid.UUID) -> None:
    """Seed the parent decisions row HITLQueueWriter's hitl_queue FK requires."""
    await admin_conn.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO decisions (
            decision_id, org_id, correlation_id, partition_key, proposing_agent,
            authority_level, action_kind, proposal_json, outcome
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9)
        """,
        decision_id, org, corr, "pk", "hook_generator_agent", "worker",
        "agent.execute", "{}", "deferred_to_human",
    )


async def _cleanup(admin_conn: object, org: str) -> None:
    for sql in (
        "DELETE FROM hitl_queue WHERE org_id=$1",
        "DELETE FROM decisions WHERE org_id=$1",
    ):
        try:
            await admin_conn.execute(sql, org)  # type: ignore[attr-defined]
        except Exception:
            pass


@requires_app_role
async def test_app_dal_row_is_schema_compatible_with_hitl_writer(app_db, admin_conn) -> None:
    org = _org()
    try:
        await _seed_tenant(admin_conn, org)
        now = datetime.now(timezone.utc)

        # --- app-layer DAL writes decisions + hitl_queue (request path) ---------
        app_dec, app_hitl, app_corr = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await PgHitlQueueRepository(app_db).enqueue(
            HitlEscalation(
                decision_id=app_dec,
                org_id=org,
                correlation_id=app_corr,
                causation_event_id=app_corr,
                partition_key=f"agent_execute:hook_generator_agent:{app_corr}",
                proposing_agent="hook_generator_agent",
                authority_level="worker",
                action_kind="agent.execute",
                proposal_json={"proposal_id": str(app_corr), "action_kind": "agent.execute"},
                outcome="deferred_to_human",
                outcome_reason="external_publication",
                policy_version="mvp-inline-1.0",
                score_json=None,
                governance_token_id=None,
                hitl_id=app_hitl,
                trigger_reason="first_external_launch",
                expires_at=now + timedelta(hours=48),
                created_at=now,
            )
        )

        # --- async HITLQueueWriter writes hitl_queue (seed its parent decision) --
        w_dec, w_hitl, w_corr = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await _seed_decision(admin_conn, org, w_dec, w_corr)
        ctx = DecisionContext(
            event_id=str(w_corr), tenant_id=org, department="creative",
            event_type="agent.execute", payload={"k": "v"}, received_at=now, steps=[],
        )
        result = BusDecisionResult(
            decision_id=str(w_dec), event_id=str(w_corr), tenant_id=org,
            outcome=DecisionOutcome.DEFERRED_TO_HUMAN, scoring=None, capital=None,
            final_reason="external_publication", steps=[], evaluated_at=now,
            policy_version="mvp-inline-1.0",
        )
        settings = DecisionEngineSettings(
            langfuse_public_key="x", langfuse_secret_key="y", database_url=APP_DB_URL,
        )
        await HITLQueueWriter(db=app_db, redis=AsyncMock(), settings=settings).write_escalation(
            ctx, result, w_hitl
        )

        # --- compare the two hitl_queue rows -----------------------------------
        async with app_db.tenant_session(org) as conn:
            app_row = await conn.fetchrow("SELECT * FROM hitl_queue WHERE hitl_id=$1", app_hitl)
            w_row = await conn.fetchrow("SELECT * FROM hitl_queue WHERE hitl_id=$1", w_hitl)

        assert app_row is not None and w_row is not None

        # Same columns populated (non-null) by both writers.
        for col in _POPULATED:
            assert app_row[col] is not None, f"app DAL left {col} NULL"
            assert w_row[col] is not None, f"HITLQueueWriter left {col} NULL"

        # Same columns left NULL (untouched until a human acts) by both.
        for col in _NULL_UNTIL_VERDICT:
            assert app_row[col] is None, f"app DAL populated {col}"
            assert w_row[col] is None, f"HITLQueueWriter populated {col}"

        # Same status value, and the same runtime type per shared column.
        assert app_row["status"] == w_row["status"] == "pending"
        for col in app_row.keys():
            if app_row[col] is not None and w_row[col] is not None:
                assert type(app_row[col]) is type(w_row[col]), (
                    f"{col}: {type(app_row[col])} vs {type(w_row[col])}"
                )
    finally:
        await _cleanup(admin_conn, org)
