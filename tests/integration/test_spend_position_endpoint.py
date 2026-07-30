"""GET /api/v1/spend/position on a live app — REAL Postgres, RLS-subject role.

The console's spend-against-ceiling readout. Proven here:

  * org scoping: org A's position never leaks into org B's (asymmetric seeded
    numbers on both sides), org_id comes only from the authenticated principal
    (a smuggled ?org_id= query parameter is ignored), and the connected role is
    neither superuser nor the owner of ai_cost_ledger / org_spend_ceiling — so
    RLS actually binds, as the ai_cost_ledger RLS suite proves.
  * missing ceiling: the response says so EXPLICITLY (ceiling_configured=false,
    null ceiling/remaining, a legible fail-closed detail) — never a silent zero.
  * RBAC mirrors the audit read route: owner/admin only; viewer gets 403.

All prices are EXPLICITLY SYNTHETIC (SYNTH_*), never real provider prices.
Skipped unless SKYLIZE_TEST_DB_URL (+ APP_DB_URL) are set.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from skylize.app.audit.service import AuditService
from skylize.bootstrap import Container, build_container
from skylize.config import Settings
from skylize.dal.connection import Database
from skylize.dal.cost_ledger import CostLedgerDAL, CostObservation
from skylize.dal.memory import InMemoryAuditRepository
from skylize.dal.org_spend_ceiling import OrgSpendCeilingDAL
from skylize.edge.rate_limit import RateLimiter
from skylize.edge.routes import spend as spend_routes
from skylize.events.memory_bus import InMemoryEventBus

from .conftest import (
    APP_DB_URL,
    DB_URL,
    REDIS_URL,
    TEST_JWT_SECRET,
    install_dev_header_auth,
    requires_app_role,
)
from .test_agent_execute_governed_e2e import _gen_key

pytestmark = pytest.mark.integration

# Synthetic micro-USD/Mtok prices — NOT real provider prices. 3 u/tok in, 15 u/tok out.
SYNTH_IN = 3_000_000
SYNTH_OUT = 15_000_000
_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _hdr(org: str, roles: str = "owner") -> dict[str, str]:
    return {"X-Dev-Org": org, "X-Dev-User": "u1", "X-Dev-Roles": roles}


async def _seed_tenant(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _seed_global_price(admin_conn, provider: str, model: str) -> None:
    await admin_conn.execute(
        """
        INSERT INTO model_pricing (org_id, provider, model,
            input_price_micros_per_mtok, output_price_micros_per_mtok,
            currency, version, effective_from)
        VALUES (NULL, $1, $2, $3, $4, 'USD', 1, $5)
        """,
        provider, model, SYNTH_IN, SYNTH_OUT, _EPOCH,
    )


async def _seed_ceiling(app_db: Database, org: str, ceiling_micros: int) -> None:
    await OrgSpendCeilingDAL(app_db).set_ceiling(
        org_id=org,
        billing_period=_period(),
        ceiling_micros=ceiling_micros,
        audit=AuditService(InMemoryEventBus(), InMemoryAuditRepository()),
        correlation_id=uuid.uuid4(),
    )


async def _record_spend(
    app_db: Database, org: str, provider: str, model: str, *, tokens_in: int, key: str
) -> None:
    await CostLedgerDAL(app_db).record_cost(
        CostObservation(
            org_id=org,
            correlation_id=uuid.uuid4(),
            agent_id="agent_x",
            run_id=uuid.uuid4(),
            provider=provider,
            model=model,
            input_tokens=tokens_in,
            output_tokens=0,
            occurred_at=datetime.now(timezone.utc),
            billing_period=_period(),
            idempotency_key=key,
        )
    )


async def _cleanup(admin_conn, orgs: list[str], provider: str) -> None:
    # ai_cost_ledger is append-only (row DELETE blocked by trigger even for a
    # superuser); TRUNCATE bypasses row-level triggers, as the ledger suite does.
    await admin_conn.execute("TRUNCATE ai_cost_ledger")
    await admin_conn.execute("DELETE FROM model_pricing WHERE provider = $1", provider)
    await admin_conn.execute(
        "DELETE FROM org_spend_ceiling WHERE org_id = ANY($1::text[])", orgs
    )
    await admin_conn.execute("DELETE FROM tenants WHERE org_id = ANY($1::text[])", orgs)


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
async def _running() -> AsyncIterator[tuple[AsyncClient, Container]]:
    settings = Settings(
        backend="postgres",
        # dev_auth is refused on a non-memory backend; the X-Dev-* headers
        # these cases send are honoured by install_dev_header_auth below.
        dev_auth=False,
        jwt_secret=TEST_JWT_SECRET,
        db_url=DB_URL,
        db_app_url=APP_DB_URL,
        redis_url=REDIS_URL,
        llm_demo_mode=True,
        governance_signing_key_pem=_gen_key(),
    )
    container = await build_container(settings)
    app = FastAPI()
    app.state.container = container
    install_dev_header_auth(app)
    app.state.rate_limiter = RateLimiter(10_000)
    app.state.credential_resolve_limiter = RateLimiter(10_000)
    app.include_router(spend_routes.router)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, container
    finally:
        await container.aclose()


# ---------------------------------------------------------------------------
# Org scoping — A's position never leaks into B's; the role is RLS-subject.
# ---------------------------------------------------------------------------

@requires_app_role
async def test_spend_position_is_org_scoped(app_db, admin_conn) -> None:
    s = uuid.uuid4().hex[:8]
    org_a, org_b = f"spend_a_{s}", f"spend_b_{s}"
    provider, model = f"synthprov_{uuid.uuid4().hex[:6]}", "synth-model"
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        await _seed_global_price(admin_conn, provider, model)
        await _seed_ceiling(app_db, org_a, 1_000_000)
        await _seed_ceiling(app_db, org_b, 555_000)
        # Asymmetric spend: A = 1_000 in-tokens -> 3_000 micros; B = 9_000 -> 27_000.
        await _record_spend(app_db, org_a, provider, model, tokens_in=1_000, key=f"a_{s}")
        await _record_spend(app_db, org_b, provider, model, tokens_in=9_000, key=f"b_{s}")

        # The role RLS must bind for: neither superuser nor bypassrls, and not
        # the owner of either source table (the ai_cost_ledger RLS proof).
        async with app_db.tenant_session(org_a) as conn:
            attrs = await conn.fetchrow(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            assert attrs is not None
            assert attrs["rolsuper"] is False, "test role must not be superuser"
            assert attrs["rolbypassrls"] is False, "test role must not bypass RLS"
            current = await conn.fetchval("SELECT current_user")
            for table in ("ai_cost_ledger", "org_spend_ceiling"):
                owner = await conn.fetchval(
                    "SELECT pg_get_userbyid(relowner) FROM pg_class "
                    "WHERE relname = $1 AND relnamespace = 'public'::regnamespace",
                    table,
                )
                assert owner != current, f"test role must not own {table}"

        async with _running() as (client, _):
            ra = await client.get("/api/v1/spend/position", headers=_hdr(org_a))
            assert ra.status_code == 200, ra.text
            a = ra.json()
            assert a["billing_period"] == _period()
            assert a["period_to_date_micros"] == 3_000
            assert a["ceiling_configured"] is True
            assert a["ceiling_micros"] == 1_000_000
            assert a["remaining_micros"] == 1_000_000 - 3_000

            rb = await client.get("/api/v1/spend/position", headers=_hdr(org_b))
            assert rb.status_code == 200, rb.text
            b = rb.json()
            assert b["period_to_date_micros"] == 27_000
            assert b["ceiling_micros"] == 555_000
            assert b["remaining_micros"] == 555_000 - 27_000

            # org_id comes from the principal only: a smuggled query parameter
            # naming org A changes nothing for a caller authenticated as B.
            rq = await client.get(
                f"/api/v1/spend/position?org_id={org_a}", headers=_hdr(org_b)
            )
            assert rq.status_code == 200, rq.text
            assert rq.json() == b
    finally:
        await _cleanup(admin_conn, [org_a, org_b], provider)


# ---------------------------------------------------------------------------
# Missing ceiling — explicit, legible, never a silent zero.
# ---------------------------------------------------------------------------

@requires_app_role
async def test_missing_ceiling_is_explicit_not_silent_zero(app_db, admin_conn) -> None:
    org = f"spend_c_{uuid.uuid4().hex[:8]}"
    try:
        await _seed_tenant(admin_conn, org)
        async with _running() as (client, _):
            r = await client.get("/api/v1/spend/position", headers=_hdr(org))
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ceiling_configured"] is False
            assert body["ceiling_micros"] is None, "must be null, not a fabricated 0"
            assert body["remaining_micros"] is None, "must be null, not a fabricated 0"
            assert body["period_to_date_micros"] == 0
            detail = body["detail"]
            assert detail, "missing ceiling must carry a legible explanation"
            assert "no org spend ceiling configured" in detail
            assert "refused" in detail  # a missing ceiling refuses every call (D6)
    finally:
        await _cleanup(admin_conn, [org], "none")


# ---------------------------------------------------------------------------
# RBAC mirrors the audit read route: owner/admin only.
# ---------------------------------------------------------------------------

@requires_app_role
async def test_spend_position_requires_owner_or_admin(app_db, admin_conn) -> None:
    org = f"spend_r_{uuid.uuid4().hex[:8]}"
    try:
        await _seed_tenant(admin_conn, org)
        async with _running() as (client, _):
            r = await client.get("/api/v1/spend/position", headers=_hdr(org, roles="viewer"))
            assert r.status_code == 403, r.text
    finally:
        await _cleanup(admin_conn, [org], "none")
