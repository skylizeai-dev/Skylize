"""Unit tests for GET /api/v1/billing/usage — shape, units, and honesty.

DB-free. The two RLS-scoped DALs are replaced by in-memory fakes, so what is
proven here is the ROUTE's contract; the real DAL against real Postgres (and
therefore the RLS scoping the aggregation depends on) is proven in
tests/integration/test_billing_usage_pg.py.

Three things this file exists to pin down:

  * THE HONESTY GATE. The response must carry NO plan tier, NO invoice rows, NO
    seat count and NO agent-slot count — not even as a null, because a null
    reads as "we have this concept and it is unset" when the truth is that
    nothing in the repo backs it. Their structural absence is asserted by name.
  * UNITS (ADR-0006). Every money field is named ``*_micros`` and is
    micro-currency. There is no bare ``cost``/``amount``/``*_cents`` field to be
    mistaken for minor units — a 10,000x error.
  * ZERO IS A REAL ANSWER. ``model_pricing`` ships empty by design, so an org
    with no ledger rows must get a truthful zeroed current period and an empty
    breakdown, never a fabricated figure and never an error.

All token counts and prices below are EXPLICITLY SYNTHETIC fixtures, not real
provider usage or prices.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import skylize.config as config_module
from skylize.config import Settings
from skylize.dal.cost_ledger import ModelSpend, PeriodSpend
from skylize.edge.gateway import create_app
from skylize.edge.rate_limit import RateLimiter
from skylize.edge.routes import billing as billing_routes

ORG = "org_a"
_HDR = {"X-Dev-Org": ORG, "X-Dev-User": "u1", "X-Dev-Roles": "owner"}

#: The route's DALs are absent on the memory backend, so a handler that RUNS
#: ends at this code — which means auth AND RBAC both passed (the same reading
#: as tests/unit/test_autonomy_api_key_auth.py).
_AUTH_PASSED = 503


def _period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SKYLIZE_JWT_SECRET", "test-secret")
    config_module._settings = None
    yield
    config_module._settings = None


# ---------------------------------------------------------------------------
# Fakes — stand in for the two RLS-scoped DALs the route reads.
# ---------------------------------------------------------------------------


class FakeCostLedger:
    """Records the org/period it was asked for, returns canned aggregates."""

    def __init__(
        self,
        history: list[PeriodSpend] | None = None,
        models: list[ModelSpend] | None = None,
    ) -> None:
        self._history = history or []
        self._models = models or []
        self.history_calls: list[tuple[str, int]] = []
        self.breakdown_calls: list[tuple[str, str]] = []

    async def org_period_history(self, org_id: str, *, limit: int = 12):
        self.history_calls.append((org_id, limit))
        return self._history[:limit]

    async def org_period_model_breakdown(self, org_id: str, billing_period: str):
        self.breakdown_calls.append((org_id, billing_period))
        return self._models


class FakeCeilingDAL:
    def __init__(self, ceiling_micros: int | None) -> None:
        self._ceiling = ceiling_micros
        self.calls: list[tuple[str, str]] = []

    async def read_ceiling_micros(self, org_id: str, billing_period: str):
        self.calls.append((org_id, billing_period))
        return self._ceiling


class FakeContainer:
    """Only what the billing route + `get_context` actually touch.

    ``settings`` is a REAL ``Settings`` on the memory backend with ``dev_auth``
    on, so the X-Dev-* headers below resolve through the production
    ``build_request_context`` rather than a stubbed auth path — the RBAC these
    tests assert is therefore the real dependency chain, not a fake of it.
    ``api_keys`` is None because no case here presents an X-API-Key.
    """

    def __init__(self, cost_ledger, spend_ceiling_dal) -> None:
        self.cost_ledger = cost_ledger
        self.spend_ceiling_dal = spend_ceiling_dal
        self.settings = Settings(
            backend="memory", dev_auth=True, jwt_secret="test-secret"
        )
        self.api_keys = None


def _client(cost_ledger, ceiling_dal) -> TestClient:
    """Mount ONLY the billing router over a fake container + dev-header auth."""
    app = FastAPI()
    app.state.container = FakeContainer(cost_ledger, ceiling_dal)
    app.state.rate_limiter = RateLimiter(10_000)
    app.state.credential_resolve_limiter = RateLimiter(10_000)
    app.include_router(billing_routes.router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# The honesty gate — no plan, no invoices, no seats, no slots.
# ---------------------------------------------------------------------------


def test_response_has_no_plan_invoice_seat_or_slot_fields() -> None:
    """Absent STRUCTURALLY — no field at all, not a null that reads as 'unset'."""
    ledger = FakeCostLedger()
    with _client(ledger, FakeCeilingDAL(None)) as client:
        body = client.get("/api/v1/billing/usage", headers=_HDR).json()

    forbidden = (
        "plan", "plan_tier", "tier", "invoice", "invoices", "seat", "seats",
        "seat_count", "agent_slots", "slots", "renewal", "renews_at",
        "next_invoice", "forecast", "projected",
    )
    for key in body:
        for word in forbidden:
            assert word not in key.lower(), (
                f"response field {key!r} implies {word!r}, which has NO backing "
                "table in this repo — it must not be invented"
            )


def test_unavailable_sections_names_what_has_no_backend() -> None:
    """The console renders 'not available yet' FROM DATA, not a hard-coded string."""
    with _client(FakeCostLedger(), FakeCeilingDAL(None)) as client:
        body = client.get("/api/v1/billing/usage", headers=_HDR).json()

    assert set(body["unavailable_sections"]) == {
        "plan_tier",
        "invoices",
        "seats",
        "agent_slots",
    }


def test_no_money_field_is_named_in_minor_units() -> None:
    """Every money field says `_micros`. ADR-0006: micros vs cents is 10,000x."""
    ledger = FakeCostLedger(
        models=[
            ModelSpend(
                provider="synthprov", model="synth-model", cost_micros=3_000,
                input_tokens=1_000, output_tokens=0, currency="USD",
            )
        ],
    )
    with _client(ledger, FakeCeilingDAL(1_000_000)) as client:
        body = client.get("/api/v1/billing/usage", headers=_HDR).json()

    def _check(obj: dict) -> None:
        for key in obj:
            low = key.lower()
            assert not low.endswith("_cents"), f"{key} names CENTS; ledger is micros"
            assert low not in {"cost", "amount", "spend", "total"}, (
                f"{key} is a BARE money name — the unit must be in the field name"
            )

    _check(body)
    _check(body["current_period"])
    _check(body["models"][0])


# ---------------------------------------------------------------------------
# Zero is a real answer — an unpriced/unused environment, reported honestly.
# ---------------------------------------------------------------------------


def test_empty_ledger_reports_truthful_zero_not_an_error() -> None:
    """model_pricing ships EMPTY (migration 0012), so no rows is the normal case."""
    with _client(FakeCostLedger(), FakeCeilingDAL(None)) as client:
        r = client.get("/api/v1/billing/usage", headers=_HDR)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["billing_period"] == _period()
    assert body["current_period"] == {
        "billing_period": _period(),
        "cost_micros": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    assert body["models"] == []
    assert body["history"] == []


def test_missing_ceiling_is_explicit_not_a_silent_zero() -> None:
    with _client(FakeCostLedger(), FakeCeilingDAL(None)) as client:
        body = client.get("/api/v1/billing/usage", headers=_HDR).json()

    assert body["ceiling_configured"] is False
    assert body["ceiling_micros"] is None, "must be null, not a fabricated 0"
    assert body["remaining_micros"] is None, "must be null, not a fabricated 0"
    assert "no org spend ceiling configured" in body["detail"]
    assert "refused" in body["detail"]  # a missing ceiling refuses every call (D6)


# ---------------------------------------------------------------------------
# Real aggregates pass through exactly, in micros.
# ---------------------------------------------------------------------------


def test_current_period_and_remaining_are_computed_in_micros() -> None:
    now = _period()
    ledger = FakeCostLedger(
        history=[
            PeriodSpend(
                billing_period=now, cost_micros=3_000,
                input_tokens=1_000, output_tokens=0,
            ),
            PeriodSpend(
                billing_period="2020-01", cost_micros=27_000,
                input_tokens=9_000, output_tokens=0,
            ),
        ],
        models=[
            ModelSpend(
                provider="synthprov", model="synth-model", cost_micros=3_000,
                input_tokens=1_000, output_tokens=0, currency="USD",
            )
        ],
    )
    with _client(ledger, FakeCeilingDAL(1_000_000)) as client:
        body = client.get("/api/v1/billing/usage", headers=_HDR).json()

    assert body["current_period"]["cost_micros"] == 3_000
    assert body["current_period"]["input_tokens"] == 1_000
    assert body["ceiling_configured"] is True
    assert body["ceiling_micros"] == 1_000_000
    # Remaining is against the CURRENT period only — never the history total.
    assert body["remaining_micros"] == 1_000_000 - 3_000
    assert body["detail"] is None
    assert body["models"][0]["currency"] == "USD"
    # History is forwarded whole, including months before the current one.
    assert [p["billing_period"] for p in body["history"]] == [now, "2020-01"]


def test_current_period_absent_from_history_is_a_zero_row_not_a_missing_field() -> None:
    """An org that spent nothing THIS month still gets a renderable zero row."""
    ledger = FakeCostLedger(
        history=[
            PeriodSpend(
                billing_period="2020-01", cost_micros=27_000,
                input_tokens=9_000, output_tokens=0,
            ),
        ],
    )
    with _client(ledger, FakeCeilingDAL(500)) as client:
        body = client.get("/api/v1/billing/usage", headers=_HDR).json()

    assert body["current_period"]["billing_period"] == _period()
    assert body["current_period"]["cost_micros"] == 0
    # The ceiling is measured against THIS month's zero, not last decade's spend.
    assert body["remaining_micros"] == 500


def test_remaining_may_go_negative_the_ceiling_is_a_soft_cap() -> None:
    now = _period()
    ledger = FakeCostLedger(
        history=[
            PeriodSpend(
                billing_period=now, cost_micros=1_500_000,
                input_tokens=500_000, output_tokens=0,
            ),
        ],
    )
    with _client(ledger, FakeCeilingDAL(1_000_000)) as client:
        body = client.get("/api/v1/billing/usage", headers=_HDR).json()

    assert body["remaining_micros"] == -500_000, (
        "overshoot must be reported, not clamped to 0 — the gate is a soft cap"
    )


# ---------------------------------------------------------------------------
# org_id comes from the principal; the period is the same one spend.py uses.
# ---------------------------------------------------------------------------


def test_org_id_comes_from_the_principal_never_from_a_query_parameter() -> None:
    ledger = FakeCostLedger()
    ceiling = FakeCeilingDAL(None)
    with _client(ledger, ceiling) as client:
        r = client.get("/api/v1/billing/usage?org_id=org_victim", headers=_HDR)
    assert r.status_code == 200, r.text

    assert [c[0] for c in ledger.history_calls] == [ORG]
    assert [c[0] for c in ledger.breakdown_calls] == [ORG]
    assert [c[0] for c in ceiling.calls] == [ORG]


def test_breakdown_and_ceiling_use_the_current_calendar_month() -> None:
    ledger = FakeCostLedger()
    ceiling = FakeCeilingDAL(None)
    with _client(ledger, ceiling) as client:
        client.get("/api/v1/billing/usage", headers=_HDR)

    assert ledger.breakdown_calls == [(ORG, _period())]
    assert ceiling.calls == [(ORG, _period())]


# ---------------------------------------------------------------------------
# The `periods` bound.
# ---------------------------------------------------------------------------


def test_periods_defaults_to_twelve_and_is_forwarded() -> None:
    ledger = FakeCostLedger()
    with _client(ledger, FakeCeilingDAL(None)) as client:
        client.get("/api/v1/billing/usage", headers=_HDR)
        client.get("/api/v1/billing/usage?periods=3", headers=_HDR)

    assert [c[1] for c in ledger.history_calls] == [12, 3]


@pytest.mark.parametrize("value", ["0", "-1", "37", "abc"])
def test_periods_outside_the_bound_is_rejected(value: str) -> None:
    with _client(FakeCostLedger(), FakeCeilingDAL(None)) as client:
        r = client.get(f"/api/v1/billing/usage?periods={value}", headers=_HDR)
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# RBAC + backend requirement, against the REAL wired app.
# ---------------------------------------------------------------------------


@pytest.fixture()
def wired_client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c


def test_route_is_registered_on_the_real_gateway(wired_client: TestClient) -> None:
    """A 503 here means the router is mounted AND auth+RBAC passed; see above."""
    r = wired_client.get("/api/v1/billing/usage", headers=_HDR)
    assert r.status_code == _AUTH_PASSED, r.text
    assert "postgres" in r.json()["detail"]


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_owner_and_admin_may_read(wired_client: TestClient, role: str) -> None:
    r = wired_client.get(
        "/api/v1/billing/usage",
        headers={"X-Dev-Org": ORG, "X-Dev-User": "u1", "X-Dev-Roles": role},
    )
    assert r.status_code == _AUTH_PASSED, r.text


def test_viewer_is_refused(wired_client: TestClient) -> None:
    r = wired_client.get(
        "/api/v1/billing/usage",
        headers={"X-Dev-Org": ORG, "X-Dev-User": "u1", "X-Dev-Roles": "viewer"},
    )
    assert r.status_code == 403, r.text
