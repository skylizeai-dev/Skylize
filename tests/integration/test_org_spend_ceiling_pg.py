"""org_spend_ceiling integration tests — REAL Postgres, proven as the app role.

Covers the guarantees only a database can prove (owner decisions D1-D8, migration
0014 + the pre-call spend gate):
  * migration shape: FORCE RLS, tenant_isolation policy, NO append-only trigger,
    micro-USD column comment, clean SELECT/INSERT/UPDATE grants (D2/D5);
  * RLS: one org cannot read another org's ceiling, proven as a role that is
    neither superuser nor the table owner (pg_roles + pg_class);
  * DAL set/read + an AuditService record on every ceiling change (step 11);
  * org-wide aggregate across ALL providers, distinct from the provider-scoped
    method (D8);
  * boundary: one call under the ceiling succeeds, the next that would breach is
    refused with the SDK never invoked (and no ai_cost_ledger row) (steps 13-16);
  * fail-closed on a missing ceiling row (D6);
  * micro-USD unit round-trip through the ledger and the ceiling comparison (D3);
  * concurrency: observed overshoot vs the stated soft-cap guarantee (step 9).

Skipped unless SKYLIZE_TEST_DB_URL (+ APP_DB_URL) are set. All prices are
EXPLICITLY SYNTHETIC, never real provider prices; the synthetic rate is
1 micro-USD/token so a call's cost reads directly in whole tokens.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from skylize.adapters.llm.anthropic_adapter import AnthropicAdapter
from skylize.adapters.llm.spend_ceiling import (
    OrgSpendCeilingExceeded,
    SpendCeilingEnforcer,
)
from skylize.app.audit.service import AuditService
from skylize.config import Settings
from skylize.dal.connection import Database
from skylize.dal.cost_ledger import CostLedgerDAL, CostObservation
from skylize.dal.memory import InMemoryAuditRepository
from skylize.dal.org_spend_ceiling import OrgSpendCeilingDAL
from skylize.events.memory_bus import InMemoryEventBus

from .conftest import APP_DB_URL, DB_URL, requires_app_role, requires_pg

pytestmark = pytest.mark.integration

# Synthetic price: 1 micro-USD per token, in and out. So a call of I input + O
# output tokens costs exactly (I + O) micro-USD. NOT a real provider price.
SYNTH_RATE = 1_000_000  # micro-USD per 1e6 tokens == 1 micro-USD / token
_PROVIDER = "anthropic"  # the adapter's fixed provider id
_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _period() -> str:
    """The current billing period, exactly as the ledger/enforcer compute it."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _orgs() -> tuple[str, str]:
    s = uuid.uuid4().hex[:8]
    return f"ceil_a_{s}", f"ceil_b_{s}"


def _synth_model() -> str:
    return f"synthmodel_{uuid.uuid4().hex[:8]}"


async def _seed_tenant(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _seed_price(admin_conn, provider: str, model: str) -> None:
    await admin_conn.execute(
        """
        INSERT INTO model_pricing (org_id, provider, model,
            input_price_micros_per_mtok, output_price_micros_per_mtok,
            currency, version, effective_from)
        VALUES (NULL, $1, $2, $3, $3, 'USD', 1, $4)
        """,
        provider, model, SYNTH_RATE, _EPOCH,
    )


async def _seed_ceiling(admin_conn, org: str, period: str, ceiling_micros: int) -> None:
    # Seeded as the superuser admin (bypasses RLS) — the ops seeding path.
    await admin_conn.execute(
        "INSERT INTO org_spend_ceiling (org_id, billing_period, ceiling_micros) "
        "VALUES ($1,$2,$3) ON CONFLICT (org_id, billing_period) "
        "DO UPDATE SET ceiling_micros = EXCLUDED.ceiling_micros",
        org, period, ceiling_micros,
    )


async def _cleanup(admin_conn, orgs: list[str], models: list[str]) -> None:
    await admin_conn.execute("TRUNCATE ai_cost_ledger")  # append-only: DELETE is blocked
    await admin_conn.execute(
        "DELETE FROM org_spend_ceiling WHERE org_id = ANY($1::text[])", orgs
    )
    if models:
        await admin_conn.execute("DELETE FROM model_pricing WHERE model = ANY($1::text[])", models)
    await admin_conn.execute("DELETE FROM tenants WHERE org_id = ANY($1::text[])", orgs)


@pytest_asyncio.fixture()
async def app_db(migrated_public: None):
    """A ``Database`` pool connected as the NON-SUPERUSER app role (RLS-subject)."""
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")
    db = Database(APP_DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


def _settings(model: str) -> Settings:
    return Settings(  # type: ignore[arg-type]
        anthropic_api_key="sk-test",
        llm_model_default=model,
        llm_model_fast="claude-haiku-4-5-20251001",
        llm_model_reasoning="claude-opus-4-6",
        llm_price_sonnet_in=3.0, llm_price_sonnet_out=15.0,
        llm_price_haiku_in=0.80, llm_price_haiku_out=4.0,
        llm_price_opus_in=15.0, llm_price_opus_out=75.0,
    )


def _make_adapter(app_db, model: str):
    """A real, PG-backed adapter with the spend gate wired. Audit/bus are the
    in-memory doubles (the ledger + ceiling are the real Postgres parts)."""
    bus = InMemoryEventBus()
    repo = InMemoryAuditRepository()
    cost_ledger = CostLedgerDAL(app_db)
    enforcer = SpendCeilingEnforcer(
        ceiling_dal=OrgSpendCeilingDAL(app_db),
        cost_ledger=cost_ledger,
        audit=AuditService(bus, repo),
        bus=bus,
    )
    adapter = AnthropicAdapter(
        settings=_settings(model), cost_ledger=cost_ledger, spend_ceiling=enforcer
    )
    return adapter, bus, repo, cost_ledger


def _fake_message(model: str, *, in_tok: int, out_tok: int, barrier: threading.Barrier | None = None):
    """A fake Anthropic message. `barrier` (if given) blocks the call in its worker
    thread until N callers arrive — used to force concurrent gate passage."""
    def _make(*_args, **_kwargs):
        if barrier is not None:
            barrier.wait(timeout=10)
        m = MagicMock()
        block = MagicMock()
        block.type = "text"
        block.text = "ok"
        m.content = [block]
        m.usage.input_tokens = in_tok
        m.usage.output_tokens = out_tok
        m.model = model
        m.id = f"msg_{uuid.uuid4().hex}"  # unique => distinct ledger rows
        m.stop_reason = "end_turn"
        return m
    return _make


def _request(org: str, **kw):
    from skylize.adapters.llm.gateway import LLMGenerateRequest
    d = {
        "prompt": "Estimate this short prompt please.",
        "requested_max_tokens": 1_000,
        "governance_token_id": uuid.uuid4(),
        "org_id": org,
        "correlation_id": uuid.uuid4(),
        "agent_id": "agent_x",
    }
    d.update(kw)
    return LLMGenerateRequest(**d)  # type: ignore[arg-type]


async def _ledger_row_count(app_db, org: str) -> int:
    async with app_db.tenant_session(org) as conn:
        return int(await conn.fetchval("SELECT count(*) FROM ai_cost_ledger"))


# ---------------------------------------------------------------------------
# Migration shape — disposable schema, admin role (D2/D5)
# ---------------------------------------------------------------------------

@requires_pg
async def test_migration_forced_rls_no_trigger_and_unit_comment(pg_schema: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(DB_URL)
    await conn.execute(f'SET search_path TO "{pg_schema}"')
    try:
        # Table exists.
        exists = await conn.fetchval(
            "SELECT count(*) FROM pg_tables WHERE schemaname=$1 AND tablename='org_spend_ceiling'",
            pg_schema,
        )
        assert exists == 1

        # RLS ENABLE + FORCE (modelled on ai_cost_ledger).
        flags = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE c.relname='org_spend_ceiling' AND n.nspname=$1",
            pg_schema,
        )
        assert flags["relrowsecurity"] is True
        assert flags["relforcerowsecurity"] is True

        # tenant_isolation policy present.
        pol = await conn.fetchval(
            "SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE c.relname='org_spend_ceiling' AND n.nspname=$1 AND p.polname='tenant_isolation'",
            pg_schema,
        )
        assert pol == 1

        # NOT append-only: there is deliberately NO trigger (D5).
        trig = await conn.fetchval(
            "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE c.relname='org_spend_ceiling' AND n.nspname=$1 AND NOT t.tgisinternal",
            pg_schema,
        )
        assert trig == 0

        # Unit asserted in the schema itself (D3).
        comment = await conn.fetchval(
            "SELECT col_description('%s.org_spend_ceiling'::regclass, "
            "  (SELECT ordinal_position FROM information_schema.columns "
            "   WHERE table_schema='%s' AND table_name='org_spend_ceiling' "
            "     AND column_name='ceiling_micros'))" % (pg_schema, pg_schema)
        )
        assert comment is not None and "micro-USD" in comment

        # Grants in a fresh schema are exactly the migration's explicit ones:
        # SELECT/INSERT/UPDATE, and deliberately NOT DELETE.
        grants = {
            r["privilege_type"]
            for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE table_schema=$1 AND table_name='org_spend_ceiling' AND grantee='skylize_app'",
                pg_schema,
            )
        }
        assert {"SELECT", "INSERT", "UPDATE"} <= grants
        assert "DELETE" not in grants
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# RLS — cross-tenant isolation proven as a non-superuser, non-owner role (D5)
# ---------------------------------------------------------------------------

@requires_app_role
async def test_rls_blocks_cross_tenant_ceiling_read(app_db, app_conn, admin_conn) -> None:
    org_a, org_b = _orgs()
    period = _period()
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        audit = AuditService(InMemoryEventBus(), InMemoryAuditRepository())
        dal = OrgSpendCeilingDAL(app_db)
        await dal.set_ceiling(org_id=org_a, billing_period=period, ceiling_micros=111,
                              audit=audit, correlation_id=uuid.uuid4())
        await dal.set_ceiling(org_id=org_b, billing_period=period, ceiling_micros=222,
                              audit=audit, correlation_id=uuid.uuid4())

        # Bound to org_a, a raw SELECT sees ONLY org_a's ceiling row.
        async with app_db.tenant_session(org_a) as conn:
            seen = {r["org_id"] for r in await conn.fetch("SELECT org_id FROM org_spend_ceiling")}
        assert seen == {org_a}
        assert await dal.read_ceiling_micros(org_a, period) == 111
        assert await dal.read_ceiling_micros(org_b, period) == 222  # each isolated read

        # Prove the RLS-subject role is NEITHER superuser NOR the table owner —
        # otherwise RLS would be moot (exactly as the ai_cost_ledger RLS proof does).
        role = await app_conn.fetchval("SELECT current_user")
        rolrow = await admin_conn.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=$1", role
        )
        assert rolrow["rolsuper"] is False, f"{role} is a superuser — would bypass RLS"
        assert rolrow["rolbypassrls"] is False, f"{role} has BYPASSRLS — would bypass RLS"
        owner = await admin_conn.fetchval(
            "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE relname='org_spend_ceiling'"
        )
        assert owner != role, f"{role} owns the table — owner bypasses RLS unless FORCE"
    finally:
        await _cleanup(admin_conn, [org_a, org_b], [])


# ---------------------------------------------------------------------------
# DAL set/read + an audit record on every change (step 11)
# ---------------------------------------------------------------------------

@requires_app_role
async def test_dal_set_read_and_audits_every_change(app_db, admin_conn) -> None:
    org, _ = _orgs()
    period = _period()
    try:
        await _seed_tenant(admin_conn, org)
        bus = InMemoryEventBus()
        repo = InMemoryAuditRepository()
        audit = AuditService(bus, repo)
        dal = OrgSpendCeilingDAL(app_db)

        assert await dal.read_ceiling_micros(org, period) is None  # empty => None (D6)

        await dal.set_ceiling(org_id=org, billing_period=period, ceiling_micros=5_000,
                              audit=audit, correlation_id=uuid.uuid4())
        assert await dal.read_ceiling_micros(org, period) == 5_000

        await dal.set_ceiling(org_id=org, billing_period=period, ceiling_micros=9_000,
                              audit=audit, correlation_id=uuid.uuid4())  # UPDATE
        assert await dal.read_ceiling_micros(org, period) == 9_000

        # Every change is audited (governance event, not silent config).
        sets = [r for r in repo.rows if r.action_type == "governance.spend_ceiling_set"]
        assert len(sets) == 2
        assert "None -> 5000" in sets[0].result_reason
        assert "5000 -> 9000" in sets[1].result_reason
        assert len(bus.published_of_type("audit.action_recorded")) == 2

        # Negative ceiling is rejected before any write.
        with pytest.raises(ValueError):
            await dal.set_ceiling(org_id=org, billing_period=period, ceiling_micros=-1,
                                  audit=audit, correlation_id=uuid.uuid4())
    finally:
        await _cleanup(admin_conn, [org], [])


# ---------------------------------------------------------------------------
# Org-wide aggregate across ALL providers, distinct from provider-scoped (D8)
# ---------------------------------------------------------------------------

@requires_app_role
async def test_org_wide_aggregate_spans_all_providers(app_db, admin_conn) -> None:
    org, _ = _orgs()
    period = _period()
    prov2 = f"synthprov_{uuid.uuid4().hex[:6]}"
    model_a, model_b = _synth_model(), _synth_model()
    try:
        await _seed_tenant(admin_conn, org)
        await _seed_price(admin_conn, _PROVIDER, model_a)
        await _seed_price(admin_conn, prov2, model_b)
        ledger = CostLedgerDAL(app_db)

        # anthropic: 1000+1000 = 2000 micros; prov2: 500+500 = 1000 micros.
        await ledger.record_cost(CostObservation(
            org_id=org, correlation_id=uuid.uuid4(), agent_id="a", run_id=uuid.uuid4(),
            provider=_PROVIDER, model=model_a, input_tokens=1000, output_tokens=1000,
            occurred_at=datetime.now(timezone.utc), billing_period=period, idempotency_key="k1"))
        await ledger.record_cost(CostObservation(
            org_id=org, correlation_id=uuid.uuid4(), agent_id="a", run_id=uuid.uuid4(),
            provider=prov2, model=model_b, input_tokens=500, output_tokens=500,
            occurred_at=datetime.now(timezone.utc), billing_period=period, idempotency_key="k2"))

        # Org-wide (D8) spans BOTH providers.
        assert await ledger.org_period_total_micros(org, period) == 3_000
        # The existing provider-scoped method is unchanged: each provider isolated.
        assert await ledger.period_total_micros(org, _PROVIDER, period) == 2_000
        assert await ledger.period_total_micros(org, prov2, period) == 1_000
    finally:
        await _cleanup(admin_conn, [org], [model_a, model_b])


# ---------------------------------------------------------------------------
# Boundary — one call under the ceiling succeeds; the next that would breach is
# refused with the SDK never invoked and no ledger row written (steps 13-16, D3)
# ---------------------------------------------------------------------------

@requires_app_role
async def test_boundary_one_succeeds_next_refused_sdk_untouched(app_db, admin_conn) -> None:
    org, _ = _orgs()
    period = _period()
    model = _synth_model()
    try:
        await _seed_tenant(admin_conn, org)
        await _seed_price(admin_conn, _PROVIDER, model)
        # One call costs 1000+1000 = 2000 micro-USD; set the ceiling to exactly
        # that. This same number round-trips the unit (D3): ceiling_micros and
        # cost_micros are compared as the same micro-USD quantity.
        one_call_cost = 2_000
        await _seed_ceiling(admin_conn, org, period, one_call_cost)
        adapter, bus, repo, ledger = _make_adapter(app_db, model)

        # Call 1: period-to-date is 0; estimate (~1012) < 2000 -> allowed.
        with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as m1:
            m1.return_value.messages.create.side_effect = _fake_message(model, in_tok=1000, out_tok=1000)
            resp = await adapter.generate(_request(org))
            assert resp.text == "ok"
            m1.assert_called_once()  # SDK WAS invoked on the allowed call
        assert resp.cost_usd_micros == one_call_cost  # recorded in micro-USD
        assert await ledger.org_period_total_micros(org, period) == one_call_cost

        # Call 2: period-to-date is now 2000; 2000 + estimate > 2000 -> refused.
        with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as m2:
            m2.return_value.messages.create.side_effect = _fake_message(model, in_tok=1000, out_tok=1000)
            with pytest.raises(OrgSpendCeilingExceeded) as ei:
                await adapter.generate(_request(org))
            m2.assert_not_called()  # SDK NEVER invoked on the refused call
        assert ei.value.ceiling_micros == one_call_cost
        assert ei.value.period_to_date_micros == one_call_cost

        # Refusal wrote NO new ledger row (nothing was spent) and emitted both signals.
        assert await _ledger_row_count(app_db, org) == 1  # only the first, allowed call
        assert await ledger.org_period_total_micros(org, period) == one_call_cost
        assert len(bus.published_of_type("governance.scope_violation")) == 1
        assert any(r.action_type == "governance.spend_ceiling_exceeded" for r in repo.rows)
    finally:
        await _cleanup(admin_conn, [org], [model])


# ---------------------------------------------------------------------------
# Fail closed — a missing ceiling row refuses and writes no ledger row (D6, 16)
# ---------------------------------------------------------------------------

@requires_app_role
async def test_missing_ceiling_row_refuses_and_writes_no_ledger_row(app_db, admin_conn) -> None:
    org, _ = _orgs()
    model = _synth_model()
    try:
        await _seed_tenant(admin_conn, org)
        await _seed_price(admin_conn, _PROVIDER, model)
        # No ceiling seeded for the current period => fail closed.
        adapter, bus, repo, ledger = _make_adapter(app_db, model)

        with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as m:
            m.return_value.messages.create.side_effect = _fake_message(model, in_tok=1000, out_tok=1000)
            with pytest.raises(OrgSpendCeilingExceeded) as ei:
                await adapter.generate(_request(org))
            m.assert_not_called()
        assert ei.value.ceiling_micros is None  # no ceiling configured
        assert await _ledger_row_count(app_db, org) == 0  # nothing spent, nothing recorded
        assert len(bus.published_of_type("governance.scope_violation")) == 1
    finally:
        await _cleanup(admin_conn, [org], [model])


# ---------------------------------------------------------------------------
# Concurrency — observed overshoot vs the stated soft-cap guarantee (step 9)
# ---------------------------------------------------------------------------

@requires_app_role
async def test_concurrency_overshoot_within_guarantee(app_db, admin_conn) -> None:
    org, _ = _orgs()
    period = _period()
    model = _synth_model()
    concurrency = 2
    one_call_cost = 2_000  # 1000 in + 1000 out at 1 micro/token
    try:
        await _seed_tenant(admin_conn, org)
        await _seed_price(admin_conn, _PROVIDER, model)
        # Ceiling fits exactly ONE call.
        await _seed_ceiling(admin_conn, org, period, one_call_cost)
        adapter, _bus, _repo, ledger = _make_adapter(app_db, model)

        # A barrier makes every concurrent call block INSIDE the SDK stub until all
        # `concurrency` callers have passed the gate — the worst case the soft-cap
        # guarantee is about: all reads see period-to-date before any write lands.
        barrier = threading.Barrier(concurrency)

        async def _one():
            with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as m:
                m.return_value.messages.create.side_effect = _fake_message(
                    model, in_tok=1000, out_tok=1000, barrier=barrier)
                try:
                    await adapter.generate(_request(org))
                    return True
                except OrgSpendCeilingExceeded:
                    return False

        results = await asyncio.gather(*[_one() for _ in range(concurrency)])
        served = sum(results)
        total = await ledger.org_period_total_micros(org, period)
        overshoot = max(0, total - one_call_cost)

        # The stated guarantee (step 9): overshoot is bounded by roughly one
        # maximal in-flight call per concurrent run, i.e. <= (concurrency-1) calls.
        bound = (concurrency - 1) * one_call_cost
        print(
            f"\n[concurrency] served={served} total={total} micro-USD "
            f"ceiling={one_call_cost} observed_overshoot={overshoot} bound={bound}"
        )
        assert overshoot <= bound, (
            f"overshoot {overshoot} exceeds stated bound {bound} — FINDING"
        )
        # Every served call is fully recorded (no lost writes under concurrency).
        assert total == served * one_call_cost
    finally:
        await _cleanup(admin_conn, [org], [model])
