"""POST /api/v1/cowork/turns end to end — real Postgres, real HTTP, fake provider.

What this suite is actually for:

  * THE FIX. An ordinary conversational turn must return 201 and NOT open a HITL
    ticket. Before `defers_on_trigger_presence=False` (cc02003) stage 2.5
    deferred on the mere PRESENCE of a trigger on the contract, so every message
    an employee sent became a 202 and a queue row. That is the regression this
    endpoint would otherwise ship with, so it is asserted against the real route.

  * THE BINDING. The turn runs on a v1.1 token bound to the caller's own
    principal, scoped to the intersection of the contract manifest with that
    human's compiled grants -- so a principal holding one of the two manifest
    tools gets a one-tool token, not the whole manifest.

  * FAIL CLOSED. A caller whose ROLE lets them in but who has no principal
    record, or no usable grant, is refused 403 `principal_authority_denied` --
    not 500, and not silently executed on an agent-rooted token.

Real Postgres + a real local HTTP provider; skipped unless SKYLIZE_TEST_DB_URL
(+ APP_DB_URL) are set.
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

MODEL = "fake-cowork-mvp"
SYNTH_RATE = 1_000
BIG_CEILING = 10_000_000_000
_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: The employee. dev-header auth puts this in RequestContext.user_id, which the
#: route passes as `on_behalf_of_principal`, so the seeded principal_id must
#: match it exactly -- the same identity derivation migration 0020 encodes.
PRINCIPAL = "employee_devon"

TURN = {"message": "what did my agents do overnight?"}


def _org() -> str:
    return f"cowork_{uuid.uuid4().hex[:10]}"


def _headers(org: str, user: str = PRINCIPAL) -> dict[str, str]:
    return {"X-Dev-Org": org, "X-Dev-User": user, "X-Dev-Roles": "owner"}


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


# ── seeding ──────────────────────────────────────────────────────────────────

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
        org_id=org,
        billing_period=_period(),
        ceiling_micros=BIG_CEILING,
        audit=AuditService(InMemoryEventBus(), InMemoryAuditRepository()),
        correlation_id=uuid.uuid4(),
    )


async def _seed_principal(admin_conn, org: str, *, scopes: tuple[str, ...]) -> None:
    await admin_conn.execute(
        "INSERT INTO principal (principal_id, org_id, display_name, authority_level) "
        "VALUES ($1,$2,$3,$4)",
        PRINCIPAL, org, "Devon", "executive",
    )
    for scope in scopes:
        await admin_conn.execute(
            "INSERT INTO principal_grant (org_id, principal_id, scope, source, created_by) "
            "VALUES ($1,$2,$3,'position','test')",
            org, PRINCIPAL, scope,
        )


async def _cleanup(admin_conn, org: str) -> None:
    # ORDER IS LOAD-BEARING: every FK to `tenants` is NO ACTION, so children go
    # first. work_journal is append-only (DELETE is blocked by its trigger), so
    # it must be TRUNCATEd -- and BEFORE the tenants row, not after, or the
    # parent delete raises ForeignKeyViolationError on work_journal_org_id_fkey.
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
    await admin_conn.execute("TRUNCATE work_journal")  # append-only: DELETE blocked
    await admin_conn.execute("DELETE FROM model_pricing WHERE model = $1", MODEL)


async def _build_app(base_url: str, org: str, *, governed: bool):
    settings = Settings(
        backend="postgres",
        dev_auth=False,
        jwt_secret=TEST_JWT_SECRET,
        db_url=DB_URL,
        db_app_url=APP_DB_URL,
        redis_url=REDIS_URL,
        decision_engine_org_ids=[org] if governed else [],
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
    # Same as create_app() (edge/gateway.py): without this, CodedHTTPException
    # travels FastAPI's default handler and the `code` sibling key never appears,
    # so the test would assert a response shape the real gateway does not produce.
    install_error_handlers(app)
    app.state.container = container
    install_dev_header_auth(app)
    app.state.rate_limiter = RateLimiter(10_000)
    app.state.credential_resolve_limiter = RateLimiter(10_000)
    app.include_router(cowork_routes.router)
    return app, container


async def _journal_rows(app_db: Database, org: str) -> list[dict]:
    async with app_db.tenant_session(org) as conn:
        rows = await conn.fetch(
            "SELECT principal_id, actor_kind, actor_id, kind, headline "
            "FROM work_journal WHERE org_id=$1 ORDER BY seq",
            org,
        )
    return [dict(r) for r in rows]


async def _hitl_count(app_db: Database, org: str) -> int:
    async with app_db.tenant_session(org) as conn:
        return int(await conn.fetchval("SELECT count(*) FROM hitl_queue WHERE org_id=$1", org))


async def _token_scopes(app_db: Database, org: str) -> list[list[str]]:
    async with app_db.tenant_session(org) as conn:
        rows = await conn.fetch(
            "SELECT scope FROM governance_tokens WHERE org_id=$1 AND agent_id='cowork_agent'",
            org,
        )
    return [list(r["scope"]) for r in rows]


# ── the fix: an ordinary turn does NOT defer ─────────────────────────────────

@requires_app_role
async def test_ordinary_turn_returns_201_and_opens_no_hitl_ticket(
    app_db, admin_conn, fake_provider
) -> None:
    """THE regression `defers_on_trigger_presence` exists to prevent.

    The org is GOVERNED, so step 2.5 really runs -- this is not passing because
    the gate was skipped. cowork_agent still declares two human_in_loop_triggers;
    what changed is that their mere presence is no longer a request-time verdict.
    """
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        await _seed_principal(admin_conn, org, scopes=("llm.generate", "memory.search"))

        app, container = await _build_app(base_url, org, governed=True)
        fake.program(success(text=json.dumps({"reply": "Two invoices reconciled."})))
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                r = await client.post(
                    "/api/v1/cowork/turns", json=TURN, headers=_headers(org)
                )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["agent_id"] == "cowork_agent"
            assert uuid.UUID(body["deliverable_id"])
            assert "reply" in body and body["reply"]

            # The point: no ticket, for a governed org.
            assert await _hitl_count(app_db, org) == 0
        finally:
            await container.aclose()
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_turn_is_journalled_as_agent_cowork_for_the_calling_principal(
    app_db, admin_conn, fake_provider
) -> None:
    """The journal is what lets a co-work agent say 'you did this' vs 'your agent
    did this while you were away', so the actor_kind has to be right and the row
    has to be scoped to the caller's own principal."""
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        await _seed_principal(admin_conn, org, scopes=("llm.generate", "memory.search"))

        app, container = await _build_app(base_url, org, governed=False)
        fake.program(success(text=json.dumps({"reply": "ok"})))
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                r = await client.post(
                    "/api/v1/cowork/turns", json=TURN, headers=_headers(org)
                )
            assert r.status_code == 201, r.text

            rows = await _journal_rows(app_db, org)
            assert len(rows) == 1, rows
            assert rows[0]["actor_kind"] == "agent_cowork"
            assert rows[0]["principal_id"] == PRINCIPAL
            assert rows[0]["actor_id"] == "cowork_agent"
            assert rows[0]["kind"] == "cowork.turn"
        finally:
            await container.aclose()
    finally:
        await _cleanup(admin_conn, org)


# ── the binding: the token is narrowed to the human's own authority ──────────

@requires_app_role
async def test_token_scope_is_the_intersection_not_the_manifest(
    app_db, admin_conn, fake_provider
) -> None:
    """A principal holding ONE of the two manifest tools gets a ONE-tool token.

    Asserted on the persisted governance_tokens row, so it is the scope that was
    actually signed and stored -- not a value read back out of the object that
    produced it.
    """
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        await _seed_principal(admin_conn, org, scopes=("llm.generate",))

        app, container = await _build_app(base_url, org, governed=False)
        fake.program(success(text=json.dumps({"reply": "ok"})))
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                r = await client.post(
                    "/api/v1/cowork/turns", json=TURN, headers=_headers(org)
                )
            assert r.status_code == 201, r.text

            scopes = await _token_scopes(app_db, org)
            assert scopes, "no cowork token was persisted"
            for scope in scopes:
                assert scope == ["llm.generate"], scope
                assert "memory.search" not in scope
        finally:
            await container.aclose()
    finally:
        await _cleanup(admin_conn, org)


# ── fail closed ──────────────────────────────────────────────────────────────

@requires_app_role
async def test_caller_with_no_principal_record_is_refused_403(
    app_db, admin_conn, fake_provider
) -> None:
    """Role sufficient, authority absent. An unknown human has NO authority, which
    is not the same as unrestricted authority -- so this must be a typed 403 and
    the provider must never be called."""
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        # Deliberately NO principal row.

        app, container = await _build_app(base_url, org, governed=False)
        fake.program(success(text=json.dumps({"reply": "should never be produced"})))
        before = fake.attempts
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                r = await client.post(
                    "/api/v1/cowork/turns", json=TURN, headers=_headers(org)
                )
            assert r.status_code == 403, r.text
            assert r.json()["code"] == "principal_authority_denied"
            assert fake.attempts == before, "the provider was called despite refusal"

            async with app_db.tenant_session(org) as conn:
                assert await conn.fetchval(
                    "SELECT count(*) FROM deliverables WHERE org_id=$1", org
                ) == 0
        finally:
            await container.aclose()
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_principal_holding_no_manifest_tool_is_refused_403(
    app_db, admin_conn, fake_provider
) -> None:
    """A real principal whose grants do not intersect the manifest at all. An
    empty intersection is an UNAUTHORIZED session, not a degraded one, so it
    refuses rather than minting a zero-tool token."""
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        await _seed_principal(admin_conn, org, scopes=("stripe.refund",))

        app, container = await _build_app(base_url, org, governed=False)
        fake.program(success(text=json.dumps({"reply": "should never be produced"})))
        before = fake.attempts
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                r = await client.post(
                    "/api/v1/cowork/turns", json=TURN, headers=_headers(org)
                )
            assert r.status_code == 403, r.text
            assert r.json()["code"] == "principal_authority_denied"
            assert fake.attempts == before
        finally:
            await container.aclose()
    finally:
        await _cleanup(admin_conn, org)


@requires_app_role
async def test_a_caller_cannot_name_someone_else_as_the_principal(
    app_db, admin_conn, fake_provider
) -> None:
    """`principal_id` comes from the authenticated context, never the body.

    CoworkTurnIn is extra="forbid", so smuggling a principal in the payload is a
    422 rather than an impersonation -- and the seeded principal here is a
    DIFFERENT person, so if the body were honoured the call would succeed.
    """
    base_url, fake = fake_provider
    org = _org()
    try:
        await _seed_price(admin_conn)
        await _seed_tenant(admin_conn, org)
        await _seed_ceiling(app_db, org)
        await _seed_principal(admin_conn, org, scopes=("llm.generate", "memory.search"))

        app, container = await _build_app(base_url, org, governed=False)
        fake.program(success(text=json.dumps({"reply": "ok"})))
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                r = await client.post(
                    "/api/v1/cowork/turns",
                    json={**TURN, "on_behalf_of_principal": PRINCIPAL},
                    headers=_headers(org, user="someone_else"),
                )
            assert r.status_code == 422, r.text

            # And without the smuggled field, the unknown caller is still refused.
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                r2 = await client.post(
                    "/api/v1/cowork/turns", json=TURN, headers=_headers(org, user="someone_else")
                )
            assert r2.status_code == 403, r2.text
            assert r2.json()["code"] == "principal_authority_denied"
        finally:
            await container.aclose()
    finally:
        await _cleanup(admin_conn, org)
