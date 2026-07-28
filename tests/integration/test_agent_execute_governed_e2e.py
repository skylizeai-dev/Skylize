"""End-to-end: the governance decision gate on a live POST /api/v1/agents/execute.

Real app (real Container on the postgres backend) + real Postgres + the fake
Anthropic HTTP server, for hook_generator_agent, covering the governed outcomes
plus the ungoverned control:

  approve -> 201, deliverable persisted, ledger row written, SDK invoked
  defer   -> 202 with hitl_id, hitl_queue row present with that exact id,
             SDK never invoked, no deliverable, no ledger row
  ungoverned org -> 201, same body shape, executes as today (gate dormant)

The governed outcomes are driven by hook_generator_agent's
human_in_loop_triggers (owner decision K1): the production contract declares
FIRST_EXTERNAL_LAUNCH (defer, external publication); a []-trigger variant
approves; a BRAND_LEGAL_SENSITIVE variant — a trigger the synchronous vertical
cannot specifically honour — also defers, recording the trigger in
trigger_reason (owner decision 2026-07-28: fail-closed defer into the HITL
queue, superseding the K2 reject). The evaluator's rejected outcome remains
reachable only for a genuinely invalid proposal (proven at the unit level in
test_decision_evaluator.py). The audit record and the terminal decision event
are asserted for every outcome.

Real Postgres + Redis; skipped unless SKYLIZE_TEST_DB_URL (+ APP_DB_URL) are set.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from skylize.app.audit.service import AuditService
from skylize.bootstrap import build_container
from skylize.config import Settings
from skylize.contracts.base import HumanInLoopTrigger
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.connection import Database
from skylize.dal.memory import InMemoryAuditRepository
from skylize.dal.org_spend_ceiling import OrgSpendCeilingDAL
from skylize.edge.rate_limit import RateLimiter
from skylize.edge.routes import agents as agents_routes
from skylize.events.memory_bus import InMemoryEventBus
from tests.fakes.fake_provider_api import running_fake_provider, success

from .conftest import APP_DB_URL, DB_URL, REDIS_URL, requires_app_role

pytestmark = pytest.mark.integration

MODEL = "fake-haiku-mvp"
SYNTH_RATE = 1_000_000  # 1 micro-USD / token (synthetic, not a real price)
BIG_CEILING = 10**12
_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)

_INPUT = {
    "brand_name": "Acme",
    "product_description": "A revolutionary widget",
    "target_audience": "startup founders",
    "tone": "energetic",
}


def _org() -> str:
    return f"gov_{uuid.uuid4().hex[:8]}"


def _period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _owner(org: str) -> dict[str, str]:
    return {"X-Dev-Org": org, "X-Dev-User": "u1", "X-Dev-Roles": "owner"}


def _gen_key() -> str:
    key = ec.generate_private_key(ec.SECP384R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


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


async def _seed_tenant(admin_conn: object, org: str) -> None:
    await admin_conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _seed_price(admin_conn: object) -> None:
    await admin_conn.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO model_pricing (org_id, provider, model,
            input_price_micros_per_mtok, output_price_micros_per_mtok,
            currency, version, effective_from)
        VALUES (NULL, 'anthropic', $1, $2, $2, 'USD', 1, $3)
        ON CONFLICT DO NOTHING
        """,
        MODEL, SYNTH_RATE, _EPOCH,
    )


async def _seed_ceiling(app_db: Database, org: str) -> None:
    await OrgSpendCeilingDAL(app_db).set_ceiling(
        org_id=org,
        billing_period=_period(),
        ceiling_micros=BIG_CEILING,
        audit=AuditService(InMemoryEventBus(), InMemoryAuditRepository()),
        correlation_id=uuid.uuid4(),
    )


async def _deliverable_ids(app_db: Database, org: str) -> list[uuid.UUID]:
    async with app_db.tenant_session(org) as conn:
        rows = await conn.fetch("SELECT id FROM deliverables")
    return [r["id"] for r in rows]


async def _ledger_keys(app_db: Database, org: str) -> list[str]:
    async with app_db.tenant_session(org) as conn:
        rows = await conn.fetch("SELECT idempotency_key FROM ai_cost_ledger")
    return [r["idempotency_key"] for r in rows]


async def _audit_count(app_db: Database, org: str, action_type: str) -> int:
    async with app_db.tenant_session(org) as conn:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM audit_log WHERE action_type=$1", action_type
        )
    return int(n)


async def _hitl_row(app_db: Database, org: str, hitl_id: uuid.UUID) -> dict | None:
    async with app_db.tenant_session(org) as conn:
        row = await conn.fetchrow(
            "SELECT hitl_id, status, decision_id, trigger_reason "
            "FROM hitl_queue WHERE hitl_id=$1",
            hitl_id,
        )
    return dict(row) if row is not None else None


async def _cleanup(admin_conn: object, org: str) -> None:
    for sql in (
        "DELETE FROM hitl_queue WHERE org_id=$1",
        "DELETE FROM decisions WHERE org_id=$1",
        "DELETE FROM deliverables WHERE org_id=$1",
        "DELETE FROM ai_cost_ledger WHERE org_id=$1",
        "DELETE FROM org_spend_ceiling WHERE org_id=$1",
    ):
        try:
            await admin_conn.execute(sql, org)  # type: ignore[attr-defined]
        except Exception:
            pass


@requires_app_role
async def test_governed_execute_three_outcomes_e2e(app_db, admin_conn, fake_provider) -> None:
    base_url, fake = fake_provider
    gov_org, ungov_org = _org(), _org()
    orig_hook = MVP_REGISTRY.resolve("hook_generator_agent")

    settings = Settings(
        backend="postgres",
        db_url=DB_URL,
        db_app_url=APP_DB_URL,
        redis_url=REDIS_URL,
        decision_engine_org_ids=[gov_org],  # the sole switch (D3)
        anthropic_api_key="sk-test",
        anthropic_base_url=base_url,
        llm_demo_mode=False,
        governance_signing_key_pem=_gen_key(),
        llm_model_default=MODEL,
        llm_model_fast=MODEL,
        llm_model_reasoning=MODEL,
    )

    await _seed_price(admin_conn)
    for org in (gov_org, ungov_org):
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)

    container = await build_container(settings)
    app = FastAPI()
    app.state.container = container
    app.state.rate_limiter = RateLimiter(10_000)
    app.state.credential_resolve_limiter = RateLimiter(10_000)
    app.include_router(agents_routes.router)

    def _variant(triggers: list[HumanInLoopTrigger]) -> None:
        MVP_REGISTRY.register_contract(
            orig_hook.model_copy(update={"human_in_loop_triggers": triggers})
        )

    # httpx AsyncClient + ASGITransport runs the app IN-PROCESS on THIS event loop,
    # so the container's asyncpg pool (created on this loop) is the same loop the
    # route handlers run on. starlette's TestClient uses a separate portal loop and
    # would break the pool ("event loop is closed"). No lifespan runs (app.state is
    # wired manually above), which is exactly what we want.
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # ---- DEFER: production contract (FIRST_EXTERNAL_LAUNCH) -> 202 ------
            _variant([HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH])
            fake.program(success(text=json.dumps({"hooks": ["x"]}), message_id="never_defer"))
            a0 = fake.attempts
            r = await client.post(
                "/api/v1/agents/execute",
                json={"agent_id": "hook_generator_agent", "input": _INPUT},
                headers=_owner(gov_org),
            )
            assert r.status_code == 202, r.text
            hitl_id = uuid.UUID(r.json()["hitl_id"])  # raises if not a UUID
            assert r.json()["status"] == "deferred_to_human"
            assert fake.attempts == a0  # SDK never invoked
            row = await _hitl_row(app_db, gov_org, hitl_id)
            assert row is not None and row["hitl_id"] == hitl_id  # 202 id == queue row id
            assert row["status"] == "pending"
            assert row["trigger_reason"] == HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH.value
            assert await _deliverable_ids(app_db, gov_org) == []  # no deliverable
            assert await _ledger_keys(app_db, gov_org) == []  # no ledger row
            assert await _audit_count(app_db, gov_org, "decision.deferred_to_human") == 1

            # ---- DEFER (unmatched trigger): BRAND_LEGAL_SENSITIVE -> 202 -------
            # Owner decision 2026-07-28: a trigger the synchronous vertical cannot
            # specifically honour routes into the HITL queue (fail-closed defer)
            # instead of dead-ending as a 403, and trigger_reason records WHY.
            _variant([HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE])
            fake.program(success(text=json.dumps({"hooks": ["x"]}), message_id="never_defer2"))
            a0 = fake.attempts
            r = await client.post(
                "/api/v1/agents/execute",
                json={"agent_id": "hook_generator_agent", "input": _INPUT},
                headers=_owner(gov_org),
            )
            assert r.status_code == 202, r.text
            hitl_id2 = uuid.UUID(r.json()["hitl_id"])
            assert r.json()["status"] == "deferred_to_human"
            assert fake.attempts == a0  # SDK never invoked
            row2 = await _hitl_row(app_db, gov_org, hitl_id2)
            assert row2 is not None and row2["status"] == "pending"
            assert row2["trigger_reason"] == HumanInLoopTrigger.BRAND_LEGAL_SENSITIVE.value
            assert await _deliverable_ids(app_db, gov_org) == []  # no deliverable
            assert await _ledger_keys(app_db, gov_org) == []  # no ledger row
            assert await _audit_count(app_db, gov_org, "decision.deferred_to_human") == 2
            assert await _audit_count(app_db, gov_org, "decision.rejected") == 0

            # ---- APPROVE: []-trigger variant -> 201, executes -----------------
            _variant([])
            fake.program(success(
                text=json.dumps({"hooks": ["h1", "h2", "h3"]}),
                message_id="msg_ok", input_tokens=10, output_tokens=5,
            ))
            a0 = fake.attempts
            r = await client.post(
                "/api/v1/agents/execute",
                json={"agent_id": "hook_generator_agent", "input": _INPUT},
                headers=_owner(gov_org),
            )
            assert r.status_code == 201, r.text
            assert r.json()["agent_id"] == "hook_generator_agent"
            assert fake.attempts > a0  # SDK invoked
            assert len(await _deliverable_ids(app_db, gov_org)) == 1  # deliverable persisted
            assert len(await _ledger_keys(app_db, gov_org)) == 1  # ledger row written
            assert await _audit_count(app_db, gov_org, "decision.approved") == 1

            # ---- UNGOVERNED org behaves exactly as before -> 201 --------------
            _variant([HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH])  # production contract
            fake.program(success(
                text=json.dumps({"hooks": ["u1", "u2", "u3"]}),
                message_id="msg_ungov", input_tokens=10, output_tokens=5,
            ))
            r = await client.post(
                "/api/v1/agents/execute",
                json={"agent_id": "hook_generator_agent", "input": _INPUT},
                headers=_owner(ungov_org),
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert set(body.keys()) == {"deliverable_id", "status", "agent_id", "title"}
            assert body["status"] == "draft"
            assert len(await _deliverable_ids(app_db, ungov_org)) == 1
            # The gate never ran for the ungoverned org: no decision.* audit.
            assert await _audit_count(app_db, ungov_org, "decision.approved") == 0
            assert await _audit_count(app_db, ungov_org, "decision.deferred_to_human") == 0
    finally:
        MVP_REGISTRY.register_contract(orig_hook)  # restore the production contract
        await container.aclose()
        await _cleanup(admin_conn, gov_org)
        await _cleanup(admin_conn, ungov_org)
