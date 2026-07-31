"""End-to-end: the THIRD proven agent vertical — `cfo_agent`.

Before this, `cfo_agent` was the last agent reaching `create_deliverable` only
against an `AsyncMock` (tests/unit/test_finance_agent_execution.py:62) —
nothing persisted. Proving it clears the mocked-only category.

It is a genuine third vertical, not a copy of either proven one:

  1. It DEFERS. The contract declares `human_in_loop_triggers=[SPEND_OVER_CEILING,
     LOW_CONFIDENCE_IRREVERSIBLE]` (contracts/mvp/finance.py:183-186). Neither is
     FIRST_EXTERNAL_LAUNCH, and the trigger list is non-empty, so
     `DecisionEvaluator._decide_agent_execution` takes its final branch —
     "a human-in-loop condition the synchronous vertical cannot specifically
     honour still fails closed ... but routes into the HITL queue"
     (evaluator.py:241-260, owner decision 2026-07-28). So this is a 202 with a
     hitl_id, and the deliverable exists only after a human approves. That makes
     the audit chain link by `causation_id` — populated ONLY on a HITL replay
     (execution.py:377-379) — where seo_keyword_agent's approve path links by a
     shared `correlation_id` and leaves causation_id NULL.
  2. It runs the TOOL LOOP: `invocable_tools=["utility.current_datetime"]`
     (finance.py:176) is non-empty, so `execute()` takes `_execute_with_tools`
     (execution.py:233) and the provider is called TWICE.
  3. It is the ONLY agent that exercises the POST-VALIDATION RECOMPUTE
     (execution.py:342-344): after the model's output validates, `total` and
     `flags` are overwritten with values computed in Python from the customer's
     own `line_items`. Neither proven vertical touches it. This test drives it
     with a provider response whose totals are DELIBERATELY WRONG and asserts
     the persisted deliverable carries the recomputed figures — the arithmetic
     in a financial deliverable never comes from the model.

Flow: POST /api/v1/agents/execute -> 202 deferred (no deliverable, no ledger
row, provider untouched) -> POST /api/v1/hitl/{id}/approve -> tool loop runs ->
deliverable persisted -> GET /api/v1/deliverables/{id} -> 200 with the
RECOMPUTED total and flags, two ai_cost_ledger rows carrying the resolved model
id, and the audit chain linked by causation_id.

Real app (real Container on the postgres backend) + real Postgres + the fake
Anthropic HTTP server. Skipped unless SKYLIZE_TEST_DB_URL (+ APP_DB_URL) are set.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from skylize.app.agents.execution import _compute_budget_summary
from skylize.bootstrap import Container, build_container
from skylize.config import Settings
from skylize.contracts.base import HumanInLoopTrigger
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.connection import Database
from skylize.edge.rate_limit import RateLimiter
from skylize.edge.routes import agents as agents_routes
from skylize.edge.routes import deliverables as deliverables_routes
from skylize.edge.routes import hitl as hitl_routes
from skylize.schemas.agents.finance import BudgetLineItem, BudgetSummaryExecuteOut
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

AGENT = "cfo_agent"

#: The customer's budget. `paid_media` is 60% of the 100,000 total, over the
#: 40% concentration threshold `_compute_budget_summary` enforces, so exactly
#: one flag is expected and the other two categories produce none.
_CFO_INPUT: dict[str, object] = {
    "department": "marketing",
    "period": "2026-Q3",
    "line_items": [
        {"category": "paid_media", "amount": 60_000.0},
        {"category": "tooling", "amount": 25_000.0},
        {"category": "events", "amount": 15_000.0},
    ],
}

_TRUE_TOTAL = 100_000.0

#: What the fake provider returns as the agent's FINAL answer. `summary` and
#: `recommendation` are narrative and survive; `total` and `flags` are
#: ARITHMETIC and are deliberately wrong, so the assertions below can only pass
#: if the recompute really overwrote them. The shape is dictated entirely by
#: BudgetSummaryExecuteOut (extra="forbid", four required fields).
_WRONG_TOTAL = 7.77
_MODEL_INVENTED_FLAG = "tooling is 91% of total spend - fabricated by the model"
_CFO_OUT: dict[str, object] = {
    "summary": "Spend for the period tracked close to plan with one concentration risk.",
    "total": _WRONG_TOTAL,
    "flags": [_MODEL_INVENTED_FLAG],
    "recommendation": "Rebalance the largest category before the next allocation cycle.",
}


def test_fixture_payload_matches_the_real_output_schema() -> None:
    """Guard the fixture: the WRONG totals must still be schema-VALID.

    The point of this suite is that valid-but-wrong arithmetic is overwritten.
    If the payload stopped validating, the e2e below would fail as an opaque
    502 at execution.py's output validation and would prove nothing about the
    recompute. This fails here instead, with a clear message.
    """
    parsed = BudgetSummaryExecuteOut.model_validate(_CFO_OUT)
    assert parsed.total == _WRONG_TOTAL
    assert parsed.flags == [_MODEL_INVENTED_FLAG]


def test_the_recompute_and_the_model_disagree() -> None:
    """The premise, stated as an assertion rather than assumed: what Python
    computes from `line_items` differs from what the fixture provider returns.
    Were they equal, the e2e's recompute assertions would pass vacuously."""
    total, flags = _compute_budget_summary(
        [BudgetLineItem(**item) for item in _CFO_INPUT["line_items"]]  # type: ignore[union-attr,arg-type]
    )
    assert total == _TRUE_TOTAL
    assert total != _WRONG_TOTAL
    assert len(flags) == 1 and "paid_media" in flags[0]
    assert flags != _CFO_OUT["flags"]


def test_cfo_agent_contract_drives_a_defer_and_the_tool_loop() -> None:
    """The two contract facts the e2e's shape depends on, asserted directly so a
    contract change surfaces here rather than as a confusing 201-vs-202."""
    contract = MVP_REGISTRY.resolve(AGENT)
    assert contract.human_in_loop_triggers == [
        HumanInLoopTrigger.SPEND_OVER_CEILING,
        HumanInLoopTrigger.LOW_CONFIDENCE_IRREVERSIBLE,
    ]
    # Not FIRST_EXTERNAL_LAUNCH and not empty -> evaluator.py:247-260 defers.
    assert HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH not in contract.human_in_loop_triggers
    # Non-empty -> execution.py:233 takes the tool-loop branch (two provider calls).
    assert contract.invocable_tools == ["utility.current_datetime"]


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
        db_url=DB_URL,
        db_app_url=APP_DB_URL,
        redis_url=REDIS_URL,
        # The sole switch that puts this org under the synchronous gate (D3), so
        # the defer verdict below is the real evaluator's, not a bypass.
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
    app.include_router(hitl_routes.router)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        await container.aclose()


def _script() -> tuple[object, object]:
    """The two provider turns the tool loop drives.

    Turn 1 asks for `utility.current_datetime` -- the only entry of the
    contract's `invocable_tools`, and a tool the contract also GRANTS
    (finance.py:174), so the ToolProxy admits it. Its input schema has no
    fields (`CurrentDatetimeIn`) and it reads the clock, so the turn is
    hermetic. Turn 2 returns the final answer with the wrong arithmetic.
    """
    turn_one = success(
        tool_use={
            "id": "toolu_cfo_1",
            "name": "utility.current_datetime",
            "input": {},
        },
        message_id="msg_cfo_tool",
        input_tokens=14,
        output_tokens=5,
    )
    turn_two = success(
        text=json.dumps(_CFO_OUT),
        message_id="msg_cfo_final",
        input_tokens=22,
        output_tokens=11,
    )
    return turn_one, turn_two


async def _ledger_rows(app_db: Database, org: str) -> list[dict]:
    async with app_db.tenant_session(org) as conn:
        rows = await conn.fetch(
            "SELECT model, agent_id, idempotency_key, cost_micros FROM ai_cost_ledger "
            "ORDER BY created_at"
        )
    return [dict(r) for r in rows]


async def _deliverable_ids(app_db: Database, org: str) -> list[uuid.UUID]:
    async with app_db.tenant_session(org) as conn:
        rows = await conn.fetch("SELECT id FROM deliverables")
    return [r["id"] for r in rows]


async def _hitl_row(app_db: Database, org: str, hitl_id: uuid.UUID) -> dict | None:
    async with app_db.tenant_session(org) as conn:
        row = await conn.fetchrow(
            "SELECT hitl_id, status, correlation_id, trigger_reason "
            "FROM hitl_queue WHERE hitl_id=$1",
            hitl_id,
        )
    return dict(row) if row is not None else None


async def _audit(app_db: Database, org: str, action_type: str) -> list[dict]:
    async with app_db.tenant_session(org) as conn:
        rows = await conn.fetch(
            "SELECT correlation_id, causation_id, source_agent_id, result "
            "FROM audit_log WHERE action_type=$1",
            action_type,
        )
    return [dict(r) for r in rows]


@requires_app_role
async def test_cfo_agent_defers_then_approves_to_a_deliverable_with_recomputed_totals(
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
            # ---- execute: the gate DEFERS, so 202 and nothing has run --------
            a0 = fake.attempts
            r = await client.post(
                "/api/v1/agents/execute",
                json={"agent_id": AGENT, "input": _CFO_INPUT},
                headers=_owner(org),
            )
            assert r.status_code == 202, r.text
            body = r.json()
            assert body["status"] == "deferred_to_human"
            assert body["agent_id"] == AGENT
            hitl_id = uuid.UUID(body["hitl_id"])

            # No LLM call, no deliverable, no ledger row before the human acts.
            assert fake.attempts == a0
            assert await _deliverable_ids(app_db, org) == []
            assert await _ledger_rows(app_db, org) == []

            # The queue row records WHICH triggers deferred it -- both of them,
            # in contract order (evaluator.py:257 joins the trigger values).
            row = await _hitl_row(app_db, org, hitl_id)
            assert row is not None and row["status"] == "pending"
            assert row["trigger_reason"] == (
                "spend_over_ceiling, low_confidence_irreversible"
            )
            original_correlation_id = row["correlation_id"]

            # ---- a human approves: the original request replays -------------
            fake.program(*_script())
            r = await client.post(
                f"/api/v1/hitl/{hitl_id}/approve",
                json={"note": "budget reviewed"},
                headers=_owner(org),
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "approved"
            deliverable_id = r.json()["deliverable_id"]

            # The tool loop really ran: two provider calls, not one.
            assert fake.attempts == 2, (
                f"expected a tool_use turn then a final answer, got {fake.attempts} "
                "provider call(s)"
            )

            # ---- the deliverable persisted, and reads back over HTTP --------
            r = await client.get(
                f"/api/v1/deliverables/{deliverable_id}", headers=_owner(org)
            )
            assert r.status_code == 200, r.text
            got = r.json()
            assert got["id"] == deliverable_id
            assert got["org_id"] == org
            assert got["agent_id"] == AGENT
            # _AGENT_DELIVERABLE_TYPE maps this agent to "other" -- a DELIBERATE
            # entry (execution.py), not a default: the persisted vocabulary
            # (migration 0006:42-47) has no finance term for a budget summary.
            assert got["deliverable_type"] == "other"

            content = got["content_markdown"]
            assert content
            assert got["title"] == "Budget Summary — marketing 2026-Q3"

            # ---- THE POINT OF THIS SUITE: the recompute overwrote the model --
            # execution.py:342-344 replaces `total` and `flags` on the validated
            # object with values computed in Python from the customer's own
            # line_items. The provider said 7.77 with a fabricated flag.
            assert f"**Total:** ${_TRUE_TOTAL:,.2f}" in content, (
                "the persisted deliverable must carry the RECOMPUTED total, not "
                f"the model's; markdown was:\n{content}"
            )
            assert f"${_WRONG_TOTAL:,.2f}" not in content, (
                "the model's wrong total reached the persisted deliverable"
            )
            assert _MODEL_INVENTED_FLAG not in content, (
                "the model's fabricated flag reached the persisted deliverable"
            )
            expected_total, expected_flags = _compute_budget_summary(
                [BudgetLineItem(**item) for item in _CFO_INPUT["line_items"]]  # type: ignore[union-attr,arg-type]
            )
            assert expected_total == _TRUE_TOTAL
            for flag in expected_flags:
                assert flag in content, f"recomputed flag missing: {flag!r}"

            # The NARRATIVE fields are the model's and are untouched -- the
            # recompute replaces arithmetic only, and this proves it is not
            # simply discarding the response.
            assert str(_CFO_OUT["summary"]) in content
            assert str(_CFO_OUT["recommendation"]) in content

            # Metadata survived, including the replay marker this path adds and
            # the seo/hook approve paths do not (execution.py:355-356).
            meta = got["metadata_json"]
            assert meta["user_id"] == "u1"
            assert "llm_provider" in meta
            assert _CFO_INPUT.items() <= meta["input"].items()
            assert meta["replay_of_hitl_id"] == str(hitl_id)

            # The list read decodes through the same DAL row mapper.
            r = await client.get("/api/v1/deliverables", headers=_owner(org))
            assert r.status_code == 200, r.text
            assert any(d["id"] == deliverable_id for d in r.json()["data"])

            # ---- money: one ledger row per provider call, resolved model id --
            rows = await _ledger_rows(app_db, org)
            assert len(rows) == 2, (
                "the tool loop makes two provider calls and each completed call "
                f"is billed once (idempotency_key=message.id, anthropic_adapter.py:699); got {rows}"
            )
            assert {row["model"] for row in rows} == {MODEL}, (
                "the ledger must record the model the provider reported serving"
            )
            assert {row["agent_id"] for row in rows} == {AGENT}
            assert len({row["idempotency_key"] for row in rows}) == 2, (
                "each call needs its own idempotency key or one would overwrite "
                "the other"
            )

            # ---- audit: on a REPLAY the chain links by causation_id ----------
            deferred = await _audit(app_db, org, "decision.deferred_to_human")
            executed = await _audit(app_db, org, "agent.executed")
            assert len(deferred) == 1, deferred
            assert len(executed) == 1, executed
            assert executed[0]["source_agent_id"] == AGENT
            assert executed[0]["result"] == "success"
            # This is where cfo_agent's chain differs from seo_keyword_agent's.
            # The replay mints a NEW run_id, so correlation_id cannot bridge
            # defer -> approve -> execute; `causation_id` carries the ORIGINAL
            # request correlation instead (execution.py:377-379, K8). Both are
            # asserted, so a change to either field is caught.
            assert deferred[0]["correlation_id"] == original_correlation_id
            assert executed[0]["causation_id"] == original_correlation_id
            assert executed[0]["correlation_id"] != original_correlation_id
    finally:
        await _cleanup(admin_conn, org)
