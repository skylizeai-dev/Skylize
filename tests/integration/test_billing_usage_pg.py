"""Billing usage aggregation — REAL Postgres, proven as the RLS-subject role.

Covers what only a database can prove for the two new aggregation reads
(``CostLedgerDAL.org_period_history`` / ``org_period_model_breakdown``) and the
``GET /api/v1/billing/usage`` route that composes them:

  * RLS SCOPES THE AGGREGATE. Neither query carries an ``org_id`` predicate —
    the org comes ONLY from the ``tenant_session`` GUC the policy reads. That is
    invisible in a unit test with a fake DAL: if the policy were absent, or the
    connected role bypassed it, the GROUP BY would silently sum every tenant's
    money into one number. Proven with asymmetric spend on two orgs, as a role
    asserted to be neither superuser nor the owner of ai_cost_ledger.
  * REVERSALS NET OUT, AND LAND IN THE PERIOD THEY CORRECT. A reversal copies
    the original row's ``billing_period`` (cost_ledger.reverse_entry), so a
    correction reduces the month it corrects, not the month it was made.
  * GROUPING AND ORDER. History is newest-first by "%Y-%m" text order; the model
    breakdown is dearest-first with a total order, so its output is deterministic.
  * ZERO IS A REAL ANSWER. An org with no rows aggregates to an empty history
    and an empty breakdown, and the endpoint still returns 200 with a truthful
    zeroed current period.

All prices here are EXPLICITLY SYNTHETIC (SYNTH_*), never real provider prices;
``model_pricing`` ships empty by design (migration 0012 §"Seed") and this suite
seeds only its own throwaway provider.

Skipped unless SKYLIZE_TEST_DB_URL (+ SKYLIZE_TEST_APP_DB_URL) are set. The app
role must be the non-superuser, non-owner ``skylize_app`` role or the isolation
assertions prove nothing; that is asserted here, not assumed.
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
from skylize.edge.routes import billing as billing_routes
from skylize.events.memory_bus import InMemoryEventBus

from .conftest import (
    APP_DB_URL,
    DB_URL,
    REDIS_URL,
    TEST_CREDENTIAL_KEY,
    TEST_JWT_SECRET,
    install_dev_header_auth,
    requires_app_role,
    requires_redis,
)
from .test_agent_execute_governed_e2e import _gen_key

pytestmark = pytest.mark.integration

# Synthetic micro-USD/Mtok prices — NOT real provider prices. 3 u/tok in, 15 u/tok out.
SYNTH_IN = 3_000_000
SYNTH_OUT = 15_000_000
_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)

# A fixed past month used for history/reversal cases, chosen so it can never
# collide with the current period whatever day the suite runs.
OLD_PERIOD = "2020-01"


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


async def _record(
    app_db: Database,
    org: str,
    provider: str,
    model: str,
    *,
    tokens_in: int,
    key: str,
    period: str | None = None,
):
    """Record one synthetic charge and return the CostRecord."""
    return await CostLedgerDAL(app_db).record_cost(
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
            billing_period=period or _period(),
            idempotency_key=key,
        )
    )


async def _cleanup(admin_conn, orgs: list[str], providers: list[str]) -> None:
    # ai_cost_ledger is append-only (row DELETE blocked by the trigger even for a
    # superuser); TRUNCATE bypasses row-level triggers, as the ledger suite does.
    await admin_conn.execute("TRUNCATE ai_cost_ledger")
    await admin_conn.execute(
        "DELETE FROM model_pricing WHERE provider = ANY($1::text[])", providers
    )
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
        # dev_auth is refused on a non-memory backend; the X-Dev-* headers these
        # cases send are honoured by install_dev_header_auth below.
        dev_auth=False,
        jwt_secret=TEST_JWT_SECRET,
        credential_encryption_key=TEST_CREDENTIAL_KEY,
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
    app.include_router(billing_routes.router)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, container
    finally:
        await container.aclose()


# ---------------------------------------------------------------------------
# RLS scopes the aggregate — the guarantee a fake DAL cannot give.
# ---------------------------------------------------------------------------


@requires_app_role
@pytest.mark.asyncio
async def test_aggregates_are_rls_scoped_not_summed_across_tenants(
    app_db, admin_conn
) -> None:
    s = uuid.uuid4().hex[:8]
    org_a, org_b = f"bill_a_{s}", f"bill_b_{s}"
    provider, model = f"synthprov_{uuid.uuid4().hex[:6]}", "synth-model"
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        await _seed_global_price(admin_conn, provider, model)
        # Asymmetric: A = 1_000 in-tokens -> 3_000 micros; B = 9_000 -> 27_000.
        await _record(app_db, org_a, provider, model, tokens_in=1_000, key=f"a_{s}")
        await _record(app_db, org_b, provider, model, tokens_in=9_000, key=f"b_{s}")

        # The role RLS must bind for: neither superuser nor bypassrls, and not
        # the owner of ai_cost_ledger — otherwise these assertions prove nothing.
        async with app_db.tenant_session(org_a) as conn:
            attrs = await conn.fetchrow(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            assert attrs is not None
            assert attrs["rolsuper"] is False, "test role must not be superuser"
            assert attrs["rolbypassrls"] is False, "test role must not bypass RLS"
            current = await conn.fetchval("SELECT current_user")
            owner = await conn.fetchval(
                "SELECT pg_get_userbyid(relowner) FROM pg_class "
                "WHERE relname = 'ai_cost_ledger' AND relnamespace = 'public'::regnamespace"
            )
            assert owner != current, "test role must not own ai_cost_ledger"

        dal = CostLedgerDAL(app_db)

        # The GROUP BY carries NO org predicate: if RLS did not scope it, each
        # org would see 30_000 (both tenants summed into one period row).
        hist_a = await dal.org_period_history(org_a)
        assert [(p.billing_period, p.cost_micros) for p in hist_a] == [
            (_period(), 3_000)
        ]
        assert hist_a[0].input_tokens == 1_000

        hist_b = await dal.org_period_history(org_b)
        assert [(p.billing_period, p.cost_micros) for p in hist_b] == [
            (_period(), 27_000)
        ]

        models_a = await dal.org_period_model_breakdown(org_a, _period())
        assert [(m.provider, m.model, m.cost_micros) for m in models_a] == [
            (provider, model, 3_000)
        ]
        assert models_a[0].currency == "USD"
        assert models_a[0].input_tokens == 1_000
    finally:
        await _cleanup(admin_conn, [org_a, org_b], [provider])


# ---------------------------------------------------------------------------
# Reversals net out, in the period they correct.
# ---------------------------------------------------------------------------


@requires_app_role
@pytest.mark.asyncio
async def test_reversal_nets_out_of_the_period_it_corrects(app_db, admin_conn) -> None:
    s = uuid.uuid4().hex[:8]
    org = f"bill_rev_{s}"
    provider, model = f"synthprov_{uuid.uuid4().hex[:6]}", "synth-model"
    try:
        await _seed_tenant(admin_conn, org)
        await _seed_global_price(admin_conn, provider, model)
        dal = CostLedgerDAL(app_db)

        # Two charges in a PAST period; one of them is then reversed. The
        # reversal copies the original row's billing_period, so it must reduce
        # OLD_PERIOD — not the month the correction was made.
        keep = await _record(
            app_db, org, provider, model,
            tokens_in=1_000, key=f"keep_{s}", period=OLD_PERIOD,
        )
        drop = await _record(
            app_db, org, provider, model,
            tokens_in=2_000, key=f"drop_{s}", period=OLD_PERIOD,
        )
        assert keep.cost_micros == 3_000
        assert drop.cost_micros == 6_000

        await dal.reverse_entry(org, drop.entry_id, idempotency_key=f"rev_{s}")

        history = {p.billing_period: p for p in await dal.org_period_history(org)}
        assert set(history) == {OLD_PERIOD}, (
            "the reversal must not create a row in the current month"
        )
        assert history[OLD_PERIOD].cost_micros == 3_000, "6_000 charge did not net out"
        assert history[OLD_PERIOD].input_tokens == 1_000, "tokens must net out too"

        breakdown = await dal.org_period_model_breakdown(org, OLD_PERIOD)
        assert [(m.model, m.cost_micros, m.input_tokens) for m in breakdown] == [
            (model, 3_000, 1_000)
        ]
    finally:
        await _cleanup(admin_conn, [org], [provider])


# ---------------------------------------------------------------------------
# Grouping, ordering, and the limit.
# ---------------------------------------------------------------------------


@requires_app_role
@pytest.mark.asyncio
async def test_history_is_newest_first_and_limited(app_db, admin_conn) -> None:
    s = uuid.uuid4().hex[:8]
    org = f"bill_hist_{s}"
    provider, model = f"synthprov_{uuid.uuid4().hex[:6]}", "synth-model"
    periods = ["2019-11", "2019-12", "2020-01"]
    try:
        await _seed_tenant(admin_conn, org)
        await _seed_global_price(admin_conn, provider, model)
        for i, period in enumerate(periods):
            await _record(
                app_db, org, provider, model,
                tokens_in=1_000 * (i + 1), key=f"h{i}_{s}", period=period,
            )
        dal = CostLedgerDAL(app_db)

        # "%Y-%m" is zero-padded, so DESC TEXT order IS reverse chronological —
        # note 2019-12 sorting above 2019-11 and below 2020-01.
        full = await dal.org_period_history(org)
        assert [p.billing_period for p in full] == ["2020-01", "2019-12", "2019-11"]
        assert [p.cost_micros for p in full] == [9_000, 6_000, 3_000]

        # The limit takes the NEWEST n, not an arbitrary n.
        capped = await dal.org_period_history(org, limit=2)
        assert [p.billing_period for p in capped] == ["2020-01", "2019-12"]

        with pytest.raises(ValueError, match="limit must be >= 1"):
            await dal.org_period_history(org, limit=0)
    finally:
        await _cleanup(admin_conn, [org], [provider])


@requires_app_role
@pytest.mark.asyncio
async def test_model_breakdown_is_dearest_first_and_period_scoped(
    app_db, admin_conn
) -> None:
    s = uuid.uuid4().hex[:8]
    org = f"bill_mdl_{s}"
    provider = f"synthprov_{uuid.uuid4().hex[:6]}"
    cheap, dear = "synth-cheap", "synth-dear"
    try:
        await _seed_tenant(admin_conn, org)
        for model in (cheap, dear):
            await _seed_global_price(admin_conn, provider, model)
        await _record(app_db, org, provider, cheap, tokens_in=1_000, key=f"c_{s}")
        await _record(app_db, org, provider, dear, tokens_in=5_000, key=f"d_{s}")
        # A row in ANOTHER period must not leak into this period's breakdown.
        await _record(
            app_db, org, provider, dear,
            tokens_in=100_000, key=f"old_{s}", period=OLD_PERIOD,
        )

        breakdown = await CostLedgerDAL(app_db).org_period_model_breakdown(
            org, _period()
        )
        assert [(m.model, m.cost_micros) for m in breakdown] == [
            (dear, 15_000),
            (cheap, 3_000),
        ], "dearest first, and the other period's 300_000 must be absent"
    finally:
        await _cleanup(admin_conn, [org], [provider])


# ---------------------------------------------------------------------------
# The endpoint: real aggregates, and zero as a real answer.
# ---------------------------------------------------------------------------


@requires_redis
@requires_app_role
@pytest.mark.asyncio
async def test_endpoint_reports_real_ledger_spend_against_the_ceiling(
    app_db, admin_conn
) -> None:
    s = uuid.uuid4().hex[:8]
    org_a, org_b = f"bille_a_{s}", f"bille_b_{s}"
    provider, model = f"synthprov_{uuid.uuid4().hex[:6]}", "synth-model"
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        await _seed_global_price(admin_conn, provider, model)
        await _seed_ceiling(app_db, org_a, 1_000_000)
        await _record(app_db, org_a, provider, model, tokens_in=1_000, key=f"a_{s}")
        await _record(app_db, org_b, provider, model, tokens_in=9_000, key=f"b_{s}")

        async with _running() as (client, _):
            r = await client.get("/api/v1/billing/usage", headers=_hdr(org_a))
            assert r.status_code == 200, r.text
            body = r.json()

            assert body["billing_period"] == _period()
            assert body["current_period"]["cost_micros"] == 3_000
            assert body["current_period"]["input_tokens"] == 1_000
            assert body["ceiling_configured"] is True
            assert body["ceiling_micros"] == 1_000_000
            assert body["remaining_micros"] == 1_000_000 - 3_000
            assert [(m["model"], m["cost_micros"]) for m in body["models"]] == [
                (model, 3_000)
            ]
            assert body["unavailable_sections"] == [
                "plan_tier", "invoices", "seats", "agent_slots",
            ]
            # No fabricated commercial data anywhere in the payload.
            assert not any(
                w in k.lower()
                for k in body
                for w in ("plan", "invoice", "seat", "slot", "renewal")
            )

            # org_id comes from the principal only: a smuggled query parameter
            # naming org A changes nothing for a caller authenticated as B.
            rq = await client.get(
                f"/api/v1/billing/usage?org_id={org_a}", headers=_hdr(org_b)
            )
            assert rq.status_code == 200, rq.text
            assert rq.json()["current_period"]["cost_micros"] == 27_000
    finally:
        await _cleanup(admin_conn, [org_a, org_b], [provider])


@requires_redis
@requires_app_role
@pytest.mark.asyncio
async def test_org_with_no_rows_gets_a_truthful_zero(app_db, admin_conn) -> None:
    """model_pricing ships EMPTY by design — no spend is the normal fresh state."""
    org = f"bille_z_{uuid.uuid4().hex[:8]}"
    try:
        await _seed_tenant(admin_conn, org)
        async with _running() as (client, _):
            r = await client.get("/api/v1/billing/usage", headers=_hdr(org))
            assert r.status_code == 200, r.text
            body = r.json()

            assert body["current_period"]["cost_micros"] == 0
            assert body["current_period"]["input_tokens"] == 0
            assert body["models"] == []
            assert body["history"] == []
            assert body["ceiling_configured"] is False
            assert body["ceiling_micros"] is None, "must be null, not a fabricated 0"
            assert body["remaining_micros"] is None
            assert "no org spend ceiling configured" in body["detail"]
    finally:
        await _cleanup(admin_conn, [org], ["none"])


@requires_redis
@requires_app_role
@pytest.mark.asyncio
async def test_endpoint_requires_owner_or_admin(app_db, admin_conn) -> None:
    org = f"bille_r_{uuid.uuid4().hex[:8]}"
    try:
        await _seed_tenant(admin_conn, org)
        async with _running() as (client, _):
            r = await client.get(
                "/api/v1/billing/usage", headers=_hdr(org, roles="viewer")
            )
            assert r.status_code == 403, r.text
    finally:
        await _cleanup(admin_conn, [org], ["none"])
