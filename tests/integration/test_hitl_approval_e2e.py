"""End-to-end: the HITL chain on a live app — defer, list, approve/reject, replay.

Real app (real Container on the postgres backend) + real Postgres + the fake
Anthropic HTTP server. hook_generator_agent's production contract declares
FIRST_EXTERNAL_LAUNCH, so a governed execute defers with no contract variants:

  execute -> 202 with hitl_id
  GET /api/v1/hitl               -> the pending item, org-scoped
  POST /api/v1/hitl/{id}/approve -> deliverable produced, ledger row written,
                                    status='approved', verdict recorded,
                                    decision.approved + hitl.approved emitted
  POST /api/v1/hitl/{id}/reject  -> nothing executes, status='rejected'

Also proven here: org A cannot list or act on org B's rows (plus the pg_class /
pg_roles RLS-subject facts, K5); a second approve never double-executes (the
conditional UPDATE ... WHERE status='pending' claim, item 11); an expired or
already-actioned row is a typed refusal (K9); a drifted stored payload fails
re-validation and releases the row (K7); and the ordinary execute path cannot
smuggle the gate bypass (item 10).

Real Postgres + Redis; skipped unless SKYLIZE_TEST_DB_URL (+ APP_DB_URL) are set.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from skylize.bootstrap import Container, build_container
from skylize.config import Settings
from skylize.dal.connection import Database
from skylize.edge.rate_limit import RateLimiter
from skylize.edge.routes import agents as agents_routes
from skylize.edge.routes import hitl as hitl_routes
from tests.fakes.fake_provider_api import running_fake_provider, success

from .conftest import APP_DB_URL, DB_URL, REDIS_URL, requires_app_role
from .test_agent_execute_governed_e2e import (
    _INPUT,
    MODEL,
    _audit_count,
    _cleanup,
    _deliverable_ids,
    _gen_key,
    _ledger_keys,
    _org,
    _owner,
    _seed_ceiling,
    _seed_price,
    _seed_tenant,
)

pytestmark = pytest.mark.integration

_HOOKS = json.dumps({"hooks": ["h1", "h2", "h3"]})


@pytest.fixture()
def fake_provider() -> Iterator[tuple[str, object]]:
    with running_fake_provider() as (base_url, fake):
        yield base_url, fake


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


@asynccontextmanager
async def _running(
    gov_org: str, base_url: str
) -> AsyncIterator[tuple[AsyncClient, Container]]:
    settings = Settings(
        backend="postgres",
        db_url=DB_URL,
        db_app_url=APP_DB_URL,
        redis_url=REDIS_URL,
        decision_engine_org_ids=[gov_org],
        anthropic_api_key="sk-test",
        anthropic_base_url=base_url,
        llm_demo_mode=False,
        governance_signing_key_pem=_gen_key(),
        llm_model_default=MODEL,
        llm_model_fast=MODEL,
        llm_model_reasoning=MODEL,
    )
    container = await build_container(settings)
    app = FastAPI()
    app.state.container = container
    app.state.rate_limiter = RateLimiter(10_000)
    app.state.credential_resolve_limiter = RateLimiter(10_000)
    app.include_router(agents_routes.router)
    app.include_router(hitl_routes.router)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, container
    finally:
        await container.aclose()


async def _seed(admin_conn: object, app_db: Database, *orgs: str) -> None:
    await _seed_price(admin_conn)
    for org in orgs:
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)


async def _defer(client: AsyncClient, org: str) -> uuid.UUID:
    r = await client.post(
        "/api/v1/agents/execute",
        json={"agent_id": "hook_generator_agent", "input": _INPUT},
        headers=_owner(org),
    )
    assert r.status_code == 202, r.text
    return uuid.UUID(r.json()["hitl_id"])


async def _row(app_db: Database, org: str, hitl_id: uuid.UUID) -> dict | None:
    async with app_db.tenant_session(org) as conn:
        rec = await conn.fetchrow(
            "SELECT status, verdict_by, verdict_at, verdict_json, correlation_id, "
            "trigger_reason, request_json FROM hitl_queue WHERE hitl_id=$1",
            hitl_id,
        )
    return dict(rec) if rec is not None else None


async def _audit_causations(app_db: Database, org: str, action_type: str) -> list:
    async with app_db.tenant_session(org) as conn:
        rows = await conn.fetch(
            "SELECT causation_id FROM audit_log WHERE action_type=$1", action_type
        )
    return [r["causation_id"] for r in rows]


# ── the full approve loop (hard exit gate) ──────────────────────────────────

@requires_app_role
async def test_hitl_approve_full_loop(app_db, admin_conn, fake_provider) -> None:
    base_url, fake = fake_provider
    org = _org()
    await _seed(admin_conn, app_db, org)
    try:
        async with _running(org, base_url) as (client, _):
            hitl_id = await _defer(client, org)
            assert await _deliverable_ids(app_db, org) == []
            assert await _ledger_keys(app_db, org) == []

            # GET list shows the pending item, with enough to decide.
            r = await client.get("/api/v1/hitl", headers=_owner(org))
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["pagination"]["total"] == 1
            item = body["data"][0]
            assert item["hitl_id"] == str(hitl_id)
            assert item["agent_id"] == "hook_generator_agent"
            assert item["trigger_reason"] == "first_external_launch"
            # The stored input is the VALIDATED payload (schema defaults
            # materialized), so assert the submitted fields are present verbatim.
            assert _INPUT.items() <= item["request_input"].items()

            # Approve -> the ORIGINAL request executes on the same path.
            fake.program(success(
                text=_HOOKS, message_id="msg_approve", input_tokens=10, output_tokens=5,
            ))
            a0 = fake.attempts
            r = await client.post(
                f"/api/v1/hitl/{hitl_id}/approve",
                json={"note": "looks good"},
                headers=_owner(org),
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "approved"
            deliverable_id = uuid.UUID(r.json()["deliverable_id"])
            assert fake.attempts > a0  # provider actually invoked

            # Deliverable + ledger row exist; the row records the full verdict.
            assert await _deliverable_ids(app_db, org) == [deliverable_id]
            assert len(await _ledger_keys(app_db, org)) == 1
            row = await _row(app_db, org, hitl_id)
            assert row is not None and row["status"] == "approved"
            assert row["verdict_by"] == "u1"
            assert row["verdict_at"] is not None
            verdict = json.loads(row["verdict_json"])
            assert verdict["deliverable_id"] == str(deliverable_id)
            assert verdict["note"] == "looks good"

            # Terminal event + audit, with the K8 causation chain: the
            # hitl.approved audit record's causation_id is the ORIGINAL
            # request correlation stored on the row.
            assert await _audit_count(app_db, org, "hitl.approved") == 1
            assert await _audit_causations(app_db, org, "hitl.approved") == [
                row["correlation_id"]
            ]
            assert await _audit_count(app_db, org, "decision.deferred_to_human") == 1

            # The queue is empty again.
            r = await client.get("/api/v1/hitl", headers=_owner(org))
            assert r.json()["pagination"]["total"] == 0

            # Second approve: typed 409, no double-execution (exactly one
            # deliverable, exactly one ledger row — hard exit gate).
            a1 = fake.attempts
            r = await client.post(f"/api/v1/hitl/{hitl_id}/approve", headers=_owner(org))
            assert r.status_code == 409, r.text
            assert "approved" in r.json()["detail"]
            assert fake.attempts == a1
            assert await _deliverable_ids(app_db, org) == [deliverable_id]
            assert len(await _ledger_keys(app_db, org)) == 1

            # Reject after approve: same typed already-actioned refusal.
            r = await client.post(f"/api/v1/hitl/{hitl_id}/reject", headers=_owner(org))
            assert r.status_code == 409, r.text
    finally:
        await _cleanup(admin_conn, org)


# ── the full reject loop (hard exit gate) ───────────────────────────────────

@requires_app_role
async def test_hitl_reject_full_loop(app_db, admin_conn, fake_provider) -> None:
    base_url, fake = fake_provider
    org = _org()
    await _seed(admin_conn, app_db, org)
    try:
        async with _running(org, base_url) as (client, _):
            hitl_id = await _defer(client, org)
            a0 = fake.attempts
            r = await client.post(
                f"/api/v1/hitl/{hitl_id}/reject",
                json={"note": "not aligned"},
                headers=_owner(org),
            )
            assert r.status_code == 200, r.text
            assert r.json() == {"hitl_id": str(hitl_id), "status": "rejected"}

            assert fake.attempts == a0  # provider never invoked
            assert await _deliverable_ids(app_db, org) == []  # no deliverable
            assert await _ledger_keys(app_db, org) == []  # no ledger row
            row = await _row(app_db, org, hitl_id)
            assert row is not None and row["status"] == "rejected"
            assert row["verdict_by"] == "u1"
            assert await _audit_count(app_db, org, "hitl.rejected") == 1
            # (The terminal DecisionRejected EVENT goes to the bus — asserted
            # at the unit level; the audit_log record here is hitl.rejected.)

            # Approve after reject: typed already-actioned refusal.
            r = await client.post(f"/api/v1/hitl/{hitl_id}/approve", headers=_owner(org))
            assert r.status_code == 409, r.text
            assert await _deliverable_ids(app_db, org) == []
    finally:
        await _cleanup(admin_conn, org)


# ── tenant isolation (hard exit gate) + K5 RLS-subject facts ────────────────

@requires_app_role
async def test_org_a_cannot_list_or_act_on_org_b_rows(
    app_db, admin_conn, fake_provider
) -> None:
    base_url, _ = fake_provider
    org_a, org_b = _org(), _org()
    await _seed(admin_conn, app_db, org_a, org_b)
    try:
        async with _running(org_a, base_url) as (client, _):
            hitl_id = await _defer(client, org_a)

            # B's queue is empty; B cannot approve or reject A's row.
            r = await client.get("/api/v1/hitl", headers=_owner(org_b))
            assert r.status_code == 200 and r.json()["pagination"]["total"] == 0
            r = await client.post(f"/api/v1/hitl/{hitl_id}/approve", headers=_owner(org_b))
            assert r.status_code == 404, r.text
            r = await client.post(f"/api/v1/hitl/{hitl_id}/reject", headers=_owner(org_b))
            assert r.status_code == 404, r.text

            # A's row is untouched.
            row = await _row(app_db, org_a, hitl_id)
            assert row is not None and row["status"] == "pending"

        # K5: the table is a genuine RLS subject for the app role —
        # ENABLE + FORCE flags set, and skylize_app is neither superuser nor
        # the table owner (the ai_cost_ledger-style role-based proof).
        flags = await admin_conn.fetchrow(  # type: ignore[attr-defined]
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname='hitl_queue' AND relnamespace='public'::regnamespace"
        )
        assert flags["relrowsecurity"] is True
        assert flags["relforcerowsecurity"] is True
        app_role = await admin_conn.fetchrow(  # type: ignore[attr-defined]
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname='skylize_app'"
        )
        assert app_role is not None
        assert app_role["rolsuper"] is False and app_role["rolbypassrls"] is False
        owner = await admin_conn.fetchval(  # type: ignore[attr-defined]
            "SELECT tableowner FROM pg_tables "
            "WHERE schemaname='public' AND tablename='hitl_queue'"
        )
        assert owner != "skylize_app"
    finally:
        await _cleanup(admin_conn, org_a)
        await _cleanup(admin_conn, org_b)


# ── exactly-once under true concurrency (item 11) ───────────────────────────

@requires_app_role
async def test_simultaneous_approves_execute_exactly_once(
    app_db, admin_conn, fake_provider
) -> None:
    base_url, fake = fake_provider
    org = _org()
    await _seed(admin_conn, app_db, org)
    try:
        async with _running(org, base_url) as (client, _):
            hitl_id = await _defer(client, org)
            fake.program(success(
                text=_HOOKS, message_id="msg_race", input_tokens=10, output_tokens=5,
            ))

            r1, r2 = await asyncio.gather(
                client.post(f"/api/v1/hitl/{hitl_id}/approve", headers=_owner(org)),
                client.post(f"/api/v1/hitl/{hitl_id}/approve", headers=_owner(org)),
            )
            codes = sorted([r1.status_code, r2.status_code])
            assert codes == [200, 409], (r1.text, r2.text)

            # Exactly one deliverable and exactly one ledger row.
            assert len(await _deliverable_ids(app_db, org)) == 1
            assert len(await _ledger_keys(app_db, org)) == 1
            row = await _row(app_db, org, hitl_id)
            assert row is not None and row["status"] == "approved"
    finally:
        await _cleanup(admin_conn, org)


# ── K9: an expired row is refused with a typed error ────────────────────────

@requires_app_role
async def test_expired_row_refused(app_db, admin_conn, fake_provider) -> None:
    base_url, fake = fake_provider
    org = _org()
    await _seed(admin_conn, app_db, org)
    try:
        async with _running(org, base_url) as (client, _):
            hitl_id = await _defer(client, org)
            await admin_conn.execute(  # type: ignore[attr-defined]
                "UPDATE hitl_queue SET expires_at = now() - interval '1 hour' "
                "WHERE hitl_id=$1",
                hitl_id,
            )
            a0 = fake.attempts
            r = await client.post(f"/api/v1/hitl/{hitl_id}/approve", headers=_owner(org))
            assert r.status_code == 410, r.text
            assert fake.attempts == a0
            assert await _deliverable_ids(app_db, org) == []
            row = await _row(app_db, org, hitl_id)
            assert row is not None and row["status"] == "pending"  # not corrupted
    finally:
        await _cleanup(admin_conn, org)


# ── item 10: the ordinary path cannot set the gate bypass ───────────────────

@requires_app_role
async def test_ordinary_execute_path_cannot_set_bypass(
    app_db, admin_conn, fake_provider
) -> None:
    base_url, _ = fake_provider
    org = _org()
    await _seed(admin_conn, app_db, org)
    try:
        async with _running(org, base_url) as (client, _):
            # A body that tries to smuggle the bypass is refused at the edge
            # (ExecuteAgentRequest is extra="forbid").
            for extra in ({"hitl_approval": {"hitl_id": str(uuid.uuid4())}},
                          {"gate_bypass": True}):
                r = await client.post(
                    "/api/v1/agents/execute",
                    json={"agent_id": "hook_generator_agent", "input": _INPUT, **extra},
                    headers=_owner(org),
                )
                assert r.status_code == 422, r.text

            # And the ordinary path still defers: the gate ran.
            hitl_id = await _defer(client, org)
            row = await _row(app_db, org, hitl_id)
            assert row is not None and row["status"] == "pending"
            assert await _deliverable_ids(app_db, org) == []
    finally:
        await _cleanup(admin_conn, org)


# ── K7: schema-drifted stored payload fails loudly, row released ────────────

@requires_app_role
async def test_drifted_request_json_fails_and_releases_row(
    app_db, admin_conn, fake_provider
) -> None:
    base_url, fake = fake_provider
    org = _org()
    await _seed(admin_conn, app_db, org)
    try:
        async with _running(org, base_url) as (client, _):
            hitl_id = await _defer(client, org)
            # Simulate drift: the stored input no longer satisfies the CURRENT
            # schema (empty object misses required fields).
            await admin_conn.execute(  # type: ignore[attr-defined]
                "UPDATE hitl_queue "
                "SET request_json = jsonb_set(request_json, '{input}', '{}'::jsonb) "
                "WHERE hitl_id=$1",
                hitl_id,
            )
            a0 = fake.attempts
            r = await client.post(f"/api/v1/hitl/{hitl_id}/approve", headers=_owner(org))
            assert r.status_code == 422, r.text
            assert fake.attempts == a0  # nothing executed
            assert await _deliverable_ids(app_db, org) == []
            assert await _ledger_keys(app_db, org) == []
            row = await _row(app_db, org, hitl_id)
            assert row is not None and row["status"] == "pending"  # actionable again
            assert row["verdict_by"] is None  # claim fully released
            assert await _audit_count(app_db, org, "hitl.approve_failed") == 1
    finally:
        await _cleanup(admin_conn, org)
