"""Q5, closed for the LIVE endpoint: SCOPE denies, step 2.5 never sees it.

The design note (docs/architecture/principal_dal_and_hitl_per_turn.md) found that
stage 2.5 was never the load-bearing check for the agent.execute vertical -- it
runs before the mint and before the model, against a proposal carrying no spend,
no scope and no security verdict, so trigger PRESENCE is all it can observe. That
finding is what justified `defers_on_trigger_presence=False` on cowork_agent.

A finding in a document is a claim. This module makes it an observation about real
traffic:

  * a principal is granted ONLY `llm.generate`, so the minted token's scope is
    ["llm.generate"] -- `memory.search` is in the contract manifest but NOT in
    this human's authority;
  * the model then really asks for `memory.search` (the fake provider emits a
    tool_use block);
  * the call is refused at ValidationStage.SCOPE inside ToolProxy.invoke
    (contracts/token.py:400-410, tools/proxy.py:123-130), which writes a
    `tool.invoked` audit row with result='denied' and a reason naming the stage;
  * and the turn is NOT deferred, NOT rejected, and produces no hitl_queue row --
    step 2.5 approved it, exactly as the design note predicted, and the token
    pipeline is what actually stopped the action.

The last bullet is the point. If step 2.5 had been the thing protecting this
vertical, relaxing it would have opened a hole. It was not, and this proves the
hole is not there: the denial still happens, one layer down, where the facts are.

`memory.search` is used because it is a REGISTERED tool
(skylize.tools.builtin.default_tool_registry). An unregistered id would be
refused earlier by ToolNotRegistered (tools/proxy.py:103-111) and would prove
nothing about SCOPE. `llm.generate` is deliberately NOT a proxied tool -- it is
the LLM egress itself -- which is why the manifest's other entry is the one that
can be attempted.
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
from skylize.dal.connection import Database
from skylize.dal.memory import InMemoryAuditRepository
from skylize.dal.org_spend_ceiling import OrgSpendCeilingDAL
from skylize.edge.errors import install_error_handlers
from skylize.edge.rate_limit import RateLimiter
from skylize.edge.routes import cowork as cowork_routes
from skylize.events.memory_bus import InMemoryEventBus

from .conftest import (
    APP_DB_URL,
    DB_URL,
    REDIS_URL,
    TEST_JWT_SECRET,
    install_dev_header_auth,
    requires_app_role,
)
from ..fakes.fake_provider_api.app import success
from ..fakes.fake_provider_api.server import running_fake_provider

pytestmark = pytest.mark.integration

MODEL = "fake-cowork-scope"
SYNTH_RATE = 1_000
BIG_CEILING = 10_000_000_000
_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
PRINCIPAL = "employee_devon"
TURN = {"message": "search my memory for the invoice"}

#: In the contract manifest, registered as a real tool, and NOT granted below.
OUT_OF_SCOPE_TOOL = "memory.search"


def _org() -> str:
    return f"scope_{uuid.uuid4().hex[:10]}"


def _headers(org: str) -> dict[str, str]:
    return {"X-Dev-Org": org, "X-Dev-User": PRINCIPAL, "X-Dev-Roles": "owner"}


def _gen_key() -> str:
    key = ec.generate_private_key(ec.SECP384R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _period() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


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


async def _seed_tenant(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _seed_price(admin_conn) -> None:
    await admin_conn.execute(
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
        org_id=org, billing_period=_period(), ceiling_micros=BIG_CEILING,
        audit=AuditService(InMemoryEventBus(), InMemoryAuditRepository()),
        correlation_id=uuid.uuid4(),
    )


async def _seed_principal_with_only_llm(admin_conn, org: str) -> None:
    """One grant. `memory.search` is deliberately withheld."""
    await admin_conn.execute(
        "INSERT INTO principal (principal_id, org_id, display_name, authority_level) "
        "VALUES ($1,$2,$3,'executive')",
        PRINCIPAL, org, "Devon",
    )
    await admin_conn.execute(
        "INSERT INTO principal_grant (org_id, principal_id, scope, source, created_by) "
        "VALUES ($1,$2,'llm.generate','position','test')",
        org, PRINCIPAL,
    )


async def _cleanup(admin_conn, org: str) -> None:
    await admin_conn.execute("TRUNCATE ai_cost_ledger")
    await admin_conn.execute("TRUNCATE work_journal")
    for sql in (
        "DELETE FROM journal_cursor WHERE org_id=$1",
        "DELETE FROM principal_grant WHERE org_id=$1",
        "DELETE FROM principal WHERE org_id=$1",
        "DELETE FROM hitl_queue WHERE org_id=$1",
        "DELETE FROM decisions WHERE org_id=$1",
        "DELETE FROM deliverables WHERE org_id=$1",
        "DELETE FROM org_spend_ceiling WHERE org_id=$1",
        "DELETE FROM governance_tokens WHERE org_id=$1",
        "DELETE FROM tenants WHERE org_id=$1",
    ):
        await admin_conn.execute(sql, org)
    await admin_conn.execute("DELETE FROM model_pricing WHERE model=$1", MODEL)


async def _build(base_url: str, org: str, *, governed: bool):
    settings = Settings(
        backend="postgres", dev_auth=False, jwt_secret=TEST_JWT_SECRET,
        db_url=DB_URL, db_app_url=APP_DB_URL, redis_url=REDIS_URL,
        decision_engine_org_ids=[org] if governed else [],
        anthropic_api_key="sk-test", anthropic_base_url=base_url,
        llm_demo_mode=False, governance_signing_key_pem=_gen_key(),
        llm_model_default=MODEL, llm_model_fast=MODEL, llm_model_reasoning=MODEL,
    )
    container = await build_container(settings)
    app = FastAPI()
    install_error_handlers(app)
    app.state.container = container
    install_dev_header_auth(app)
    app.state.rate_limiter = RateLimiter(10_000)
    app.state.credential_resolve_limiter = RateLimiter(10_000)
    app.include_router(cowork_routes.router)
    return app, container


async def _denied_tool_audits(app_db: Database, org: str) -> list[dict]:
    async with app_db.tenant_session(org) as conn:
        rows = await conn.fetch(
            "SELECT action_type, result, result_reason FROM audit_log "
            "WHERE org_id=$1 AND action_type='tool.invoked' AND result='denied'",
            org,
        )
    return [dict(r) for r in rows]


async def _decision_audits(app_db: Database, org: str) -> list[str]:
    async with app_db.tenant_session(org) as conn:
        rows = await conn.fetch(
            "SELECT action_type FROM audit_log WHERE org_id=$1 "
            "AND action_type LIKE 'decision.%'",
            org,
        )
    return [r["action_type"] for r in rows]


async def _hitl_count(app_db: Database, org: str) -> int:
    async with app_db.tenant_session(org) as conn:
        return int(await conn.fetchval(
            "SELECT count(*) FROM hitl_queue WHERE org_id=$1", org
        ))


@requires_app_role
async def test_out_of_scope_tool_via_chat_is_denied_at_the_scope_stage(
    app_db, admin_conn, fake_provider
) -> None:
    """The whole claim, in one governed run."""
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        await _seed_principal_with_only_llm(admin_conn, org)

        app, container = await _build(base_url, org, governed=True)
        # Turn 1: the model demands a tool this human does not hold.
        # Turn 2: having been told no, it answers in text.
        fake.program(
            success(
                tool_use={
                    "id": "toolu_scope_1",
                    "name": OUT_OF_SCOPE_TOOL,
                    "input": {"query": "invoice"},
                },
                message_id="msg_tool_attempt",
            ),
            success(
                text=json.dumps({"reply": "I could not search your memory."}),
                message_id="msg_after_denial",
            ),
        )
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                r = await client.post(
                    "/api/v1/cowork/turns", json=TURN, headers=_headers(org)
                )
            assert r.status_code == 201, r.text

            # 1. The tool call really was attempted, and really was DENIED, and the
            #    audit names the stage that denied it.
            denials = await _denied_tool_audits(app_db, org)
            assert len(denials) == 1, denials
            reason = denials[0]["result_reason"] or ""
            assert reason.startswith("scope:"), reason
            assert OUT_OF_SCOPE_TOOL in reason

            # 2. It was NOT step 2.5. The gate ran (governed org) and APPROVED.
            decisions = await _decision_audits(app_db, org)
            assert decisions == ["decision.approved"], decisions
            assert await _hitl_count(app_db, org) == 0

            # 3. The signed token never carried the tool in the first place.
            async with app_db.tenant_session(org) as conn:
                scopes = [
                    list(row["scope"])
                    for row in await conn.fetch(
                        "SELECT scope FROM governance_tokens WHERE org_id=$1", org
                    )
                ]
            assert scopes, "no token was persisted"
            for scope in scopes:
                assert OUT_OF_SCOPE_TOOL not in scope, scope
        finally:
            await container.aclose()
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_the_same_tool_succeeds_once_the_human_actually_holds_it(
    app_db, admin_conn, fake_provider
) -> None:
    """The control. Without this, the test above could be passing because
    `memory.search` is broken for everyone rather than because THIS human lacks
    it -- which would prove nothing about scope at all."""
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        await _seed_principal_with_only_llm(admin_conn, org)
        # The ONLY difference from the case above.
        await admin_conn.execute(
            "INSERT INTO principal_grant (org_id, principal_id, scope, source, created_by) "
            "VALUES ($1,$2,$3,'position','test')",
            org, PRINCIPAL, OUT_OF_SCOPE_TOOL,
        )

        app, container = await _build(base_url, org, governed=True)
        fake.program(
            success(
                tool_use={
                    "id": "toolu_scope_2",
                    "name": OUT_OF_SCOPE_TOOL,
                    "input": {"query": "invoice"},
                },
                message_id="msg_tool_attempt",
            ),
            success(
                text=json.dumps({"reply": "Found nothing in memory."}),
                message_id="msg_after_tool",
            ),
        )
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                r = await client.post(
                    "/api/v1/cowork/turns", json=TURN, headers=_headers(org)
                )
            assert r.status_code == 201, r.text

            assert await _denied_tool_audits(app_db, org) == []
            async with app_db.tenant_session(org) as conn:
                scopes = [
                    list(row["scope"])
                    for row in await conn.fetch(
                        "SELECT scope FROM governance_tokens WHERE org_id=$1", org
                    )
                ]
            for scope in scopes:
                assert OUT_OF_SCOPE_TOOL in scope, scope
        finally:
            await container.aclose()
    finally:
        await _cleanup(admin_conn, org)
