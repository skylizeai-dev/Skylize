"""Model routing — the parts provable without a database.

The RLS and migration-shape guarantees need real Postgres and live in
``tests/integration/test_model_routing_pg.py``, which SKIPS without
``SKYLIZE_TEST_DB_URL`` / ``SKYLIZE_TEST_APP_DB_URL``. These tests run in the
unit gate unconditionally, so the fail-closed rule and the no-fabrication rule
are proven on every CI run rather than only on a Postgres-equipped one.

Proven here:
  * the three logical names are exactly the adapter's own ``_model_map`` keys,
    so the table's CHECK and the console's closed set cannot drift from what
    the gateway actually accepts;
  * the catalogue reports the CONCRETE model ids Settings holds, not names
    invented anywhere in the console;
  * an UNPRICED model reports ``pricing=None`` and never a zero -- the whole
    point, since ``model_pricing`` is seeded empty by design;
  * a missing routing row resolves to the IDENTITY mapping with no fallback,
    and stays distinguishable from a configured identity mapping;
  * ``set_rules`` rejects a bad class, target or self-fallback BEFORE touching
    the DB, and writes one generation at one instant;
  * the route 503s on the memory backend rather than inventing a catalogue;
  * the response model carries NO latency, context-window or traffic-share
    field -- a regression guard against someone adding one later.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from skylize.adapters.llm.anthropic_adapter import AnthropicAdapter
from skylize.dal.model_routing import (
    CATALOGUE_PROVIDER,
    LOGICAL_MODELS,
    ModelPrice,
    ModelRoutingDAL,
)
from skylize.edge.routes.models import ModelsResponse, get_models


class _FakeConn:
    """Returns one canned row list for every fetch the DAL issues."""

    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.rows = rows if rows is not None else []
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.queries.append((query, args))
        # The DAL issues two different fetches (prices, rules). Serve the canned
        # rows only to the one the fixture was built for; the other sees none.
        if "model_pricing" in query:
            return [r for r in self.rows if "input_price_micros_per_mtok" in r]
        return [r for r in self.rows if "routing_class" in r]

    async def execute(self, query: str, *args: object) -> None:
        self.queries.append((query, args))


class _FakeDb:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.conn = _FakeConn(rows)
        self.bound_orgs: list[str] = []

    @asynccontextmanager
    async def tenant_session(self, org_id: str):
        self.bound_orgs.append(org_id)
        yield self.conn


class _FakeAudit:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    async def record(self, **kwargs: object) -> None:
        self.records.append(kwargs)


class _FakeSettings:
    """Only the three fields the catalogue reads, with deliberately distinct
    values so a swapped mapping shows up as a failure rather than a coincidence."""

    llm_model_default = "concrete-default-id"
    llm_model_fast = "concrete-fast-id"
    llm_model_reasoning = "concrete-reasoning-id"


class _Ctx:
    def __init__(self, org_id: str = "org-1") -> None:
        self.org_id = org_id
        self.correlation_id = uuid.uuid4()


class _Container:
    def __init__(self, dal: object | None, settings: object | None = None) -> None:
        self.model_routing_dal = dal
        self.settings = settings if settings is not None else _FakeSettings()


def _price_row(model: str) -> dict[str, object]:
    return {
        "model": model,
        "input_price_micros_per_mtok": 3_000_000,
        "output_price_micros_per_mtok": 15_000_000,
        "version": 1,
        "currency": "USD",
    }


def _rule_row(
    routing_class: str, target: str, fallback: str | None = None
) -> dict[str, object]:
    return {
        "routing_class": routing_class,
        "target_logical_model": target,
        "fallback_logical_model": fallback,
        "effective_from": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }


# ---------------------------------------------------------------------------
# The logical names are the adapter's, not ours
# ---------------------------------------------------------------------------


def test_logical_models_match_the_adapters_own_map() -> None:
    """Pinned against the ADAPTER's real map, not against a copy of the list.

    ``AnthropicAdapter._model_map`` (anthropic_adapter.py:267-271) is the map
    the gateway actually enforces; ``_concrete_model``
    (anthropic_adapter.py:368-374) raises ValueError on any other name. If a
    fourth logical name is added there and not here, migration 0032's CHECK
    would reject a name the gateway accepts — this test fails first.

    Constructed with a stub Settings: the SDK clients are lazy
    (``_sync_client``/``_async_client``), so building the adapter opens no
    connection pool and needs no API key.
    """
    adapter = AnthropicAdapter(_FakeSettings())
    assert tuple(sorted(adapter._model_map)) == tuple(sorted(LOGICAL_MODELS))
    # ...and the adapter refuses anything outside them, which is why the table's
    # CHECK may be exactly this set.
    with pytest.raises(ValueError, match="unknown logical model"):
        adapter._concrete_model("atlas-4-frontier")


def test_the_catalogue_provider_is_the_adapters_own_provider_string() -> None:
    """That string is the join key into model_pricing and ai_cost_ledger
    (migration 0012). A typo here would silently price nothing."""
    assert CATALOGUE_PROVIDER == AnthropicAdapter._PROVIDER


# ---------------------------------------------------------------------------
# Catalogue — real ids, and no invented price
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalogue_reports_the_concrete_ids_from_settings() -> None:
    dal = ModelRoutingDAL(_FakeDb())
    entries = await dal.read_catalogue("org-1", _FakeSettings())
    mapping = {e.logical_name: e.concrete_model for e in entries}
    assert mapping == {
        "default": "concrete-default-id",
        "fast": "concrete-fast-id",
        "reasoning": "concrete-reasoning-id",
    }
    assert all(e.provider == "anthropic" for e in entries)


@pytest.mark.asyncio
async def test_an_unpriced_model_reports_none_and_never_a_zero() -> None:
    """model_pricing is seeded EMPTY by design (migration 0012). A zero here
    would read as 'this model is free', which is a claim nothing supports."""
    dal = ModelRoutingDAL(_FakeDb())
    entries = await dal.read_catalogue("org-1", _FakeSettings())
    assert [e.pricing for e in entries] == [None, None, None]


@pytest.mark.asyncio
async def test_a_priced_model_reports_the_real_row() -> None:
    db = _FakeDb([_price_row("concrete-fast-id")])
    dal = ModelRoutingDAL(db)
    entries = await dal.read_catalogue("org-1", _FakeSettings())
    by_name = {e.logical_name: e.pricing for e in entries}
    assert by_name["default"] is None
    assert by_name["reasoning"] is None
    assert by_name["fast"] == ModelPrice(
        input_price_micros_per_mtok=3_000_000,
        output_price_micros_per_mtok=15_000_000,
        currency="USD",
        pricing_version=1,
    )


@pytest.mark.asyncio
async def test_catalogue_read_is_bound_to_the_callers_org() -> None:
    db = _FakeDb()
    dal = ModelRoutingDAL(db)
    await dal.read_catalogue("org-7", _FakeSettings())
    assert db.bound_orgs == ["org-7"], "the read must run inside that org's session"


# ---------------------------------------------------------------------------
# Routing — fail closed to identity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_rows_resolve_to_the_identity_mapping() -> None:
    dal = ModelRoutingDAL(_FakeDb())
    rules = await dal.read_rules("org-1")
    assert [r.routing_class for r in rules] == list(LOGICAL_MODELS)
    for rule in rules:
        assert rule.target_logical_model == rule.routing_class
        assert rule.fallback_logical_model is None
        assert rule.configured is False


@pytest.mark.asyncio
async def test_unset_is_distinguishable_from_a_configured_identity_mapping() -> None:
    unset = ModelRoutingDAL(_FakeDb())
    chosen = ModelRoutingDAL(_FakeDb([_rule_row("fast", "fast")]))
    unset_fast = next(
        r for r in await unset.read_rules("org-1") if r.routing_class == "fast"
    )
    chosen_fast = next(
        r for r in await chosen.read_rules("org-1") if r.routing_class == "fast"
    )
    # Same routing to act on...
    assert unset_fast.target_logical_model == chosen_fast.target_logical_model == "fast"
    # ...different facts about whether anyone chose it.
    assert unset_fast.configured is False
    assert chosen_fast.configured is True


@pytest.mark.asyncio
async def test_a_configured_class_reads_back_exactly_and_others_stay_identity() -> None:
    db = _FakeDb([_rule_row("reasoning", "fast", "default")])
    rules = await ModelRoutingDAL(db).read_rules("org-1")
    by_class = {r.routing_class: r for r in rules}
    assert by_class["reasoning"].target_logical_model == "fast"
    assert by_class["reasoning"].fallback_logical_model == "default"
    assert by_class["reasoning"].configured is True
    # The classes the org did not configure are untouched, not blanked.
    assert by_class["default"].target_logical_model == "default"
    assert by_class["fast"].target_logical_model == "fast"


@pytest.mark.asyncio
async def test_the_rules_read_resolves_one_generation_at_one_instant() -> None:
    """Selecting the latest N rows instead would splice two decisions together
    whenever an org configured fewer than three classes."""
    db = _FakeDb()
    dal = ModelRoutingDAL(db)
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await dal.read_configured_rules("org-1", at)
    query, args = db.conn.queries[0]
    assert "SELECT MAX(effective_from)" in query
    assert "effective_from <= $2" in query
    assert args[1] == at


@pytest.mark.asyncio
async def test_resolve_class_fails_closed_to_identity() -> None:
    dal = ModelRoutingDAL(_FakeDb())
    rule = await dal.resolve_class("org-1", "reasoning")
    assert rule.target_logical_model == "reasoning"
    assert rule.fallback_logical_model is None
    assert rule.configured is False


@pytest.mark.asyncio
async def test_resolve_class_rejects_a_name_the_gateway_would_reject() -> None:
    dal = ModelRoutingDAL(_FakeDb())
    with pytest.raises(ValueError, match="routing_class must be one of"):
        await dal.resolve_class("org-1", "atlas-4-frontier")


# ---------------------------------------------------------------------------
# set_rules — validated before the DB, audited after the write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rules", "match"),
    [
        ({"nope": ("fast", None)}, "routing_class must be one of"),
        ({"fast": ("nova-2-fast", None)}, "target_logical_model must be one of"),
        ({"fast": ("default", "cirrus")}, "fallback_logical_model must be one of"),
        ({"fast": ("default", "default")}, "fallback_logical_model must differ"),
        ({}, "at least one routing class"),
    ],
)
async def test_set_rules_rejects_bad_input_before_touching_the_db(
    rules: dict[str, tuple[str, str | None]], match: str
) -> None:
    db = _FakeDb()
    dal = ModelRoutingDAL(db)
    with pytest.raises(ValueError, match=match):
        await dal.set_rules(
            org_id="org-1",
            rules=rules,
            audit=_FakeAudit(),
            correlation_id=uuid.uuid4(),
        )
    assert db.conn.queries == [], "rejected rules must not reach the database"


@pytest.mark.asyncio
async def test_set_rules_writes_one_generation_at_one_instant() -> None:
    db = _FakeDb()
    dal = ModelRoutingDAL(db)
    at = datetime(2026, 5, 1, tzinfo=timezone.utc)
    await dal.set_rules(
        org_id="org-1",
        rules={"fast": ("default", None), "reasoning": ("fast", "default")},
        audit=_FakeAudit(),
        correlation_id=uuid.uuid4(),
        effective_from=at,
    )
    inserts = [q for q in db.conn.queries if "INSERT INTO model_routing_rules" in q[0]]
    assert len(inserts) == 2
    assert {q[1][1] for q in inserts} == {at}, (
        "every class in one decision must share one effective_from"
    )


@pytest.mark.asyncio
async def test_set_rules_records_a_governance_audit_action() -> None:
    audit = _FakeAudit()
    dal = ModelRoutingDAL(_FakeDb())
    await dal.set_rules(
        org_id="org-1",
        rules={"reasoning": ("fast", None)},
        audit=audit,
        correlation_id=uuid.uuid4(),
    )
    assert len(audit.records) == 1
    assert audit.records[0]["action_type"] == "governance.model_routing_set"
    assert audit.records[0]["org_id"] == "org-1"


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_returns_the_real_catalogue_and_identity_routing() -> None:
    dal = ModelRoutingDAL(_FakeDb())
    resp = await get_models(ctx=_Ctx(), container=_Container(dal))
    assert isinstance(resp, ModelsResponse)
    assert [e.logical_name for e in resp.catalogue] == list(LOGICAL_MODELS)
    assert [e.concrete_model for e in resp.catalogue] == [
        "concrete-default-id",
        "concrete-fast-id",
        "concrete-reasoning-id",
    ]
    assert all(e.pricing is None for e in resp.catalogue)
    assert resp.pricing_configured is False
    assert all(r.configured is False for r in resp.routing)


@pytest.mark.asyncio
async def test_route_reports_pricing_configured_only_when_a_row_exists() -> None:
    dal = ModelRoutingDAL(_FakeDb([_price_row("concrete-default-id")]))
    resp = await get_models(ctx=_Ctx(), container=_Container(dal))
    assert resp.pricing_configured is True
    priced = next(e for e in resp.catalogue if e.logical_name == "default")
    assert priced.pricing is not None
    assert priced.pricing.input_price_micros_per_mtok == 3_000_000


@pytest.mark.asyncio
async def test_route_503s_without_the_postgres_backend() -> None:
    """No DAL means no routing store. Reporting a made-up catalogue would be
    worse than an error: the caller would act on names nothing wrote."""
    with pytest.raises(HTTPException) as excinfo:
        await get_models(ctx=_Ctx(), container=_Container(None))
    assert excinfo.value.status_code == 503


def test_the_response_carries_no_unsourced_field() -> None:
    """A REGRESSION GUARD, not a formality.

    The screen this route feeds previously showed latency, context-window size
    and a traffic-share percentage. Nothing in the backend measures latency or
    records a context window, and traffic share is derivable from
    ai_cost_ledger rather than configured anywhere — so a field for any of them
    could only ever be filled by hand. If someone adds one, this fails.
    """
    from skylize.edge.routes.models import (
        ModelCatalogueEntry,
        ModelRoutingRuleResponse,
    )

    forbidden = {
        "latency",
        "latency_ms",
        "p50_latency_ms",
        "p95_latency_ms",
        "context_window",
        "context_window_tokens",
        "max_context",
        "traffic_share",
        "traffic_share_pct",
        "share",
    }
    for model in (ModelsResponse, ModelCatalogueEntry, ModelRoutingRuleResponse):
        overlap = forbidden & set(model.model_fields)
        assert not overlap, f"{model.__name__} grew an unsourced field: {overlap}"
