"""End-to-end: the SECOND proven agent vertical — `seo_keyword_agent`.

Before this, exactly one of 21 agents (`hook_generator_agent`,
tests/integration/test_deliverable_readback_e2e.py) had a test proving execution
through to a deliverable persisted in Postgres and read back over HTTP.
`seo_keyword_agent` reached `create_deliverable` only against an AsyncMock
(tests/unit/test_seo_agent_execution.py:57) — nothing persisted.

This walks the real path, and it is a DIFFERENT path from hook_generator's in two
ways that make it a genuine second vertical rather than a copy:

  1. It APPROVES rather than defers. The contract declares
     `human_in_loop_triggers=[]` (contracts/mvp/seo.py:36), and
     `DecisionEvaluator._decide_agent_execution` returns `approved` for exactly
     that case (decision_engine/evaluator.py:230-240, owner decision D6
     "everything else approves"). Its `worker` authority also clears
     `authority_check`, which requires only `worker` when the proposal is not an
     external launch (evaluator.py:290). So there is no HITL hop: execute
     returns 201 and the deliverable exists immediately.
  2. It runs the TOOL LOOP, not the single-shot path. The contract declares
     `invocable_tools=["search.web", "memory.search"]` (seo.py:29), so
     `execute()` takes the `_execute_with_tools` branch (execution.py:233) and
     the provider is called TWICE — once returning a `tool_use` block, once
     returning the final answer.

Flow: POST /api/v1/agents/execute -> 201 approved -> deliverable persisted ->
GET /api/v1/deliverables/{id} -> 200 with content and metadata intact, exactly
two ai_cost_ledger rows (one per provider call) carrying the resolved model id,
and the audit chain sharing one correlation_id.

Real app (real Container on the postgres backend) + real Postgres + the fake
Anthropic HTTP server. Skipped unless SKYLIZE_TEST_DB_URL (+ APP_DB_URL) are set.
"""

from __future__ import annotations

import json
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
from skylize.schemas.agents.seo import SeoKeywordExecuteOut
from tests.fakes.fake_provider_api import running_fake_provider, success

from .conftest import (
    APP_DB_URL,
    DB_URL,
    REDIS_URL,
    TEST_CREDENTIAL_KEY,
    TEST_JWT_SECRET,
    install_dev_header_auth,
    requires_app_role,
)
from .test_agent_execute_governed_e2e import (
    MODEL,
    _cleanup,
    _gen_key,
    _org,
    _owner,
    _seed_ceiling,
    _seed_price,
    _seed_tenant,
)

pytestmark = pytest.mark.integration

AGENT = "seo_keyword_agent"

_SEO_INPUT = {
    "topic": "project management software",
    "target_market": "north america",
    "competitor_urls": ["https://example.com/competitor-a"],
}

#: What the fake provider returns as the agent's FINAL answer. The shape is
#: dictated entirely by SeoKeywordExecuteOut (extra="forbid", three required
#: fields); the strings are test fixtures, deliberately recognisable as such, and
#: are asserted to validate against the real schema below before use.
_SEO_OUT: dict[str, object] = {
    "primary_keywords": [
        "project management software for agencies",
        "best pm tool for remote teams",
    ],
    "keyword_difficulty_notes": (
        "Head terms are dominated by established review sites; the long-tail "
        "intent terms above are winnable within two quarters."
    ),
    "content_angle_suggestions": [
        "Comparison guide against the category leader",
        "Migration walkthrough for teams leaving spreadsheets",
    ],
}


def test_fixture_payload_matches_the_real_output_schema() -> None:
    """Guard the fixture: if SeoKeywordExecuteOut changes, this fails here with a
    clear message rather than as an opaque 502 inside the e2e flow below."""
    parsed = SeoKeywordExecuteOut.model_validate(_SEO_OUT)
    assert parsed.primary_keywords == _SEO_OUT["primary_keywords"]


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
async def _running(gov_org: str, base_url: str) -> AsyncIterator[AsyncClient]:
    settings = Settings(
        backend="postgres",
        # dev_auth is refused on a non-memory backend; the X-Dev-* headers below
        # are honoured by install_dev_header_auth instead.
        dev_auth=False,
        jwt_secret=TEST_JWT_SECRET,
        credential_encryption_key=TEST_CREDENTIAL_KEY,
        db_url=DB_URL,
        db_app_url=APP_DB_URL,
        redis_url=REDIS_URL,
        # The sole switch that puts this org under the synchronous gate (D3), so
        # the approve verdict below is the real evaluator's, not a bypass.
        decision_engine_org_ids=[gov_org],
        anthropic_api_key="sk-test",
        anthropic_base_url=base_url,
        llm_demo_mode=False,
        governance_signing_key_pem=_gen_key(),
        llm_model_default=MODEL,
        llm_model_fast=MODEL,
        llm_model_reasoning=MODEL,
    )
    container: Container = await build_container(settings)
    app = FastAPI()
    app.state.container = container
    install_dev_header_auth(app)
    app.state.rate_limiter = RateLimiter(10_000)
    app.state.credential_resolve_limiter = RateLimiter(10_000)
    app.include_router(agents_routes.router)
    app.include_router(deliverables_routes.router)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        await container.aclose()


def _script() -> tuple[object, object]:
    """The two provider turns the tool loop drives.

    Turn 1 asks for `search.web` -- the first entry of the contract's
    `invocable_tools`, and a tool the contract actually grants, so the ToolProxy
    admits it. With no SKYLIZE_SEARCH_API_KEY the tool resolves through
    NullWebSearchPort and returns status="not_configured" without any network
    call (tools/builtin/web_search.py:59-63,120-125), which keeps this test
    hermetic. Turn 2 returns the final answer.
    """
    turn_one = success(
        tool_use={
            "id": "toolu_seo_1",
            "name": "search.web",
            "input": {"query": "project management software keywords", "max_results": 5},
        },
        message_id="msg_seo_tool",
        input_tokens=12,
        output_tokens=6,
    )
    turn_two = success(
        text=json.dumps(_SEO_OUT),
        message_id="msg_seo_final",
        input_tokens=20,
        output_tokens=9,
    )
    return turn_one, turn_two


async def _ledger_rows(app_db: Database, org: str) -> list[dict]:
    async with app_db.tenant_session(org) as conn:
        rows = await conn.fetch(
            "SELECT model, idempotency_key, cost_micros FROM ai_cost_ledger "
            "ORDER BY created_at"
        )
    return [dict(r) for r in rows]


async def _audit(app_db: Database, org: str, action_type: str) -> list[dict]:
    async with app_db.tenant_session(org) as conn:
        rows = await conn.fetch(
            "SELECT correlation_id, causation_id, source_agent_id, result "
            "FROM audit_log WHERE action_type=$1",
            action_type,
        )
    return [dict(r) for r in rows]


@requires_app_role
async def test_seo_keyword_agent_executes_approved_and_persists_a_readable_deliverable(
    app_db, admin_conn, fake_provider
) -> None:
    base_url, fake = fake_provider
    org = _org()
    await _seed_price(admin_conn)
    await _seed_tenant(admin_conn, org)
    # Seeded through the AUDITED DAL setter, not raw SQL, so the ceiling the
    # spend gate reads was written by the real operator path.
    await _seed_ceiling(app_db, org)
    try:
        async with _running(org, base_url) as client:
            fake.program(*_script())

            # ---- execute: approved, so 201 and a deliverable straight away ----
            r = await client.post(
                "/api/v1/agents/execute",
                json={"agent_id": AGENT, "input": _SEO_INPUT},
                headers=_owner(org),
            )
            assert r.status_code == 201, r.text
            body = r.json()
            deliverable_id = body["deliverable_id"]
            assert body["agent_id"] == AGENT

            # The tool loop really ran: two provider calls, not one.
            assert fake.attempts == 2, (
                f"expected a tool_use turn then a final answer, got {fake.attempts} "
                "provider call(s)"
            )

            # ---- the deliverable persisted, and reads back over HTTP ----------
            r = await client.get(
                f"/api/v1/deliverables/{deliverable_id}", headers=_owner(org)
            )
            assert r.status_code == 200, r.text
            got = r.json()
            assert got["id"] == deliverable_id
            assert got["org_id"] == org
            assert got["agent_id"] == AGENT
            # _AGENT_DELIVERABLE_TYPE maps this agent to "seo_report"
            # (execution.py:95) -- proves the mapping is live, not just present.
            assert got["deliverable_type"] == "seo_report"

            # Content survived the round trip: the model's actual keywords are in
            # the rendered markdown, not merely some non-empty string.
            content = got["content_markdown"]
            assert content
            for keyword in _SEO_OUT["primary_keywords"]:  # type: ignore[union-attr]
                assert keyword in content, f"{keyword!r} missing from the deliverable"

            # Metadata survived: the execute path's attribution keys. No
            # replay_of_hitl_id here -- that key exists only on a HITL replay
            # (execution.py:355-356), and this agent approves outright.
            meta = got["metadata_json"]
            assert meta["user_id"] == "u1"
            assert "llm_provider" in meta
            assert _SEO_INPUT.items() <= meta["input"].items()
            assert "replay_of_hitl_id" not in meta

            # The list read decodes through the same DAL row mapper.
            r = await client.get("/api/v1/deliverables", headers=_owner(org))
            assert r.status_code == 200, r.text
            assert any(d["id"] == deliverable_id for d in r.json()["data"])

            # ---- money: one ledger row per provider call, resolved model id ---
            rows = await _ledger_rows(app_db, org)
            assert len(rows) == 2, (
                "the tool loop makes two provider calls and each completed call "
                f"is billed, so two rows are expected; got {rows}"
            )
            assert {row["model"] for row in rows} == {MODEL}, (
                "the ledger must record the model the provider reported serving"
            )
            assert len({row["idempotency_key"] for row in rows}) == 2, (
                "each call needs its own idempotency key or one would overwrite "
                "the other"
            )

            # ---- audit: the gate and the execution share one correlation ------
            approved = await _audit(app_db, org, "decision.approved")
            executed = await _audit(app_db, org, "agent.executed")
            assert len(approved) == 1, approved
            assert len(executed) == 1, executed
            assert executed[0]["source_agent_id"] == AGENT
            assert executed[0]["result"] == "success"
            # On THIS path the chain is linked by a shared correlation_id: the
            # gate verdict and the execution both carry the one `run_id` minted
            # per request (execution.py:220-225,371). `causation_id` is populated
            # ONLY on a HITL replay, where it carries the original request's
            # correlation to bridge defer -> approve -> execute (execution.py:
            # 377-379). This agent approves outright, so there is no earlier
            # request to bridge and the field is correctly NULL -- asserted, so a
            # future change that starts writing it here is caught.
            assert approved[0]["correlation_id"] == executed[0]["correlation_id"]
            assert executed[0]["causation_id"] is None
    finally:
        await _cleanup(admin_conn, org)
