"""End-to-end: the console's blocked step — deliverable read-back after approve.

GET /api/v1/deliverables/{id} returned 500 on the postgres backend because
asyncpg handed ``metadata_json`` back as ``str`` and dal/deliverables.py assumed
a dict — and every deliverable the execute path creates has non-empty metadata.
The pool's json/jsonb codec (dal.connection._init_connection) fixes the class;
this test proves the full console flow end to end:

  execute -> 202 defer -> approve -> GET /api/v1/deliverables/{id} -> 200
  with the deliverable and its metadata intact.

Real app (real Container on the postgres backend) + real Postgres + the fake
Anthropic HTTP server. Skipped unless SKYLIZE_TEST_DB_URL (+ APP_DB_URL) are set.
"""

from __future__ import annotations

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
from skylize.edge.routes import deliverables as deliverables_routes
from skylize.edge.routes import hitl as hitl_routes
from tests.fakes.fake_provider_api import running_fake_provider, success

from .conftest import (
    APP_DB_URL,
    DB_URL,
    REDIS_URL,
    TEST_JWT_SECRET,
    install_dev_header_auth,
    requires_app_role,
)
from .test_agent_execute_governed_e2e import (
    _INPUT,
    MODEL,
    _cleanup,
    _gen_key,
    _org,
    _owner,
    _seed_ceiling,
    _seed_price,
    _seed_tenant,
)
from .test_hitl_approval_e2e import _HOOKS, _defer

pytestmark = pytest.mark.integration


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
        # dev_auth is refused on a non-memory backend; the X-Dev-* headers
        # these cases send are honoured by install_dev_header_auth below.
        dev_auth=False,
        jwt_secret=TEST_JWT_SECRET,
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
    install_dev_header_auth(app)
    app.state.rate_limiter = RateLimiter(10_000)
    app.state.credential_resolve_limiter = RateLimiter(10_000)
    app.include_router(agents_routes.router)
    app.include_router(hitl_routes.router)
    app.include_router(deliverables_routes.router)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, container
    finally:
        await container.aclose()


@requires_app_role
async def test_deliverable_readback_after_approve_returns_200(
    app_db, admin_conn, fake_provider
) -> None:
    base_url, fake = fake_provider
    org = _org()
    await _seed_price(admin_conn)
    await _seed_tenant(admin_conn, org)
    await _seed_ceiling(app_db, org)
    try:
        async with _running(org, base_url) as (client, _):
            hitl_id = await _defer(client, org)

            fake.program(success(
                text=_HOOKS, message_id="msg_readback",
                input_tokens=10, output_tokens=5,
            ))
            r = await client.post(
                f"/api/v1/hitl/{hitl_id}/approve",
                json={"note": "ship it"},
                headers=_owner(org),
            )
            assert r.status_code == 200, r.text
            deliverable_id = r.json()["deliverable_id"]

            # The read that 500'd before the pool registered a JSONB codec.
            r = await client.get(
                f"/api/v1/deliverables/{deliverable_id}", headers=_owner(org)
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["id"] == deliverable_id
            assert body["org_id"] == org
            assert body["agent_id"] == "hook_generator_agent"
            assert body["content_markdown"]

            # Metadata intact: the execute path's attribution keys survive the
            # round trip (execution.py writes input/user_id/llm_provider, plus
            # replay_of_hitl_id on a HITL replay).
            meta = body["metadata_json"]
            assert meta["replay_of_hitl_id"] == str(hitl_id)
            assert meta["user_id"] == "u1"
            assert "llm_provider" in meta
            assert _INPUT.items() <= meta["input"].items()

            # The list read decodes through the same DAL row mapper.
            r = await client.get("/api/v1/deliverables", headers=_owner(org))
            assert r.status_code == 200, r.text
            assert any(d["id"] == deliverable_id for d in r.json()["data"])
    finally:
        await _cleanup(admin_conn, org)
