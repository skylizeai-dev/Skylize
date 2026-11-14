"""Governed commerce department flows (app/orchestrator/departments).

The evaluator here is the REAL inline DecisionEvaluator over the REAL governed
registry (MVP_REGISTRY); only the department tools are mocks. Every test checks
the central invariant: a tool's `execute` runs iff the verdict was `approved`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import pytest

from skylize.app.decision_engine.evaluator import (
    STAGE_AUTHORITY,
    STAGE_CAPITAL,
    STAGE_CONFLICT,
    STAGE_HITL,
    STAGE_POLICY,
    DecisionEvaluator,
)
from skylize.app.decision_engine.events import KNOWN_ACTION_KINDS, DecisionResult
from skylize.app.orchestrator.departments import (
    DEFAULT_DEPARTMENT_SPECS,
    DepartmentDeps,
    DepartmentSpec,
    ProposalDraft,
    build_department_graph,
    run_department,
    run_department_chain,
)
from skylize.contracts.base import HumanInLoopTrigger
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.memory import InMemoryCapitalRepository
from skylize.dal.ports import BudgetCeiling

ORG = "org-commerce-1"
T0 = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
SPECS = {s.department_id: s for s in DEFAULT_DEPARTMENT_SPECS}


class _Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)  # strictly increasing: recency is decidable
        return self.now


class MockTool:
    def __init__(self, draft: ProposalDraft | None = None, *, plan_exc: Exception | None = None,
                 exec_exc: Exception | None = None, result: Mapping[str, Any] | None = None) -> None:
        self.draft = draft or ProposalDraft(summary="noop", partition_key="p:default")
        self.plan_exc = plan_exc
        self.exec_exc = exec_exc
        self.result = result or {"done": True}
        self.plans: list[Mapping[str, Any]] = []
        self.executions: list[DecisionResult] = []

    async def plan(self, *, org_id: str, payload: Mapping[str, Any]) -> ProposalDraft:
        self.plans.append(payload)
        if self.plan_exc:
            raise self.plan_exc
        return self.draft

    async def execute(self, *, org_id: str, draft: ProposalDraft, decision: DecisionResult) -> Mapping[str, Any]:
        self.executions.append(decision)
        if self.exec_exc:
            raise self.exec_exc
        return self.result


class SpyEvaluator(DecisionEvaluator):
    def __init__(self, *, raise_exc: Exception | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.calls = 0
        self.raise_exc = raise_exc

    async def evaluate(self, proposal):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.raise_exc:
            raise self.raise_exc
        return await super().evaluate(proposal)


class FakeAuthority:
    """Stands in for GovernanceAuthority.assert_active (governance/authority.py:254)."""

    def __init__(self, deny: str | None = None) -> None:
        self.deny = deny
        self.checks: list[tuple[str, str]] = []

    async def assert_active(self, agent_id: str, org_id: str) -> None:
        self.checks.append((agent_id, org_id))
        if self.deny:
            raise PermissionError(self.deny)


async def _launched_before(org_id: str, spec: DepartmentSpec, draft: ProposalDraft) -> bool:
    return True


def _capital(**ceilings: int) -> InMemoryCapitalRepository:
    cap = InMemoryCapitalRepository()
    for scope, ceiling in ceilings.items():
        cap.set_ceiling(BudgetCeiling(org_id=ORG, scope=scope, ceiling_minor_units=ceiling))
    return cap


def _evaluator(capital: InMemoryCapitalRepository | None = None, **kw: Any) -> SpyEvaluator:
    return SpyEvaluator(registry=MVP_REGISTRY, capital=capital or _capital(), **kw)


async def _run(dept: str, tool: MockTool, evaluator: DecisionEvaluator, *, authority: FakeAuthority | None = None,
               launch_history: Any = None, **kw: Any):
    deps = DepartmentDeps(evaluator=evaluator, tool=tool, authority=authority or FakeAuthority(),
                          launch_history=launch_history, now_fn=_Clock())
    return await run_department(SPECS[dept], deps, org_id=ORG, **kw)


def _assert_invariant(result, tool: MockTool) -> None:
    approved = result.decision is not None and result.decision.outcome == "approved"
    assert (len(tool.executions) == 1) == approved
    assert (result.status == "executed") == approved


# ---------------------------------------------------------------------------
# Department contracts
# ---------------------------------------------------------------------------

def test_default_specs_map_onto_existing_governed_agents_and_action_kinds() -> None:
    assert [s.department_id for s in DEFAULT_DEPARTMENT_SPECS] == [
        "market_intelligence", "creative_ops", "marketing_ops", "finance_risk",
    ]
    for spec in DEFAULT_DEPARTMENT_SPECS:
        MVP_REGISTRY.resolve(spec.proposing_agent_id)  # raises if not governed
        assert spec.action_kind in KNOWN_ACTION_KINDS
    assert [s.department_id for s in DEFAULT_DEPARTMENT_SPECS if s.provisional] == ["market_intelligence"]


def test_unknown_action_kind_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="owner decision"):
        DepartmentSpec(department_id="x", proposing_agent_id="ceo", action_kind="dropship.order", capital_scope="growth")


def test_draft_spend_and_currency_travel_together() -> None:
    with pytest.raises(ValueError):
        ProposalDraft(summary="s", partition_key="p", spend_minor_units=100)


def test_graph_nodes_are_explicit() -> None:
    graph = build_department_graph(SPECS["creative_ops"], DepartmentDeps(evaluator=_evaluator(), tool=MockTool(), authority=FakeAuthority()))
    assert set(graph.get_graph().nodes) >= {"plan", "govern", "act", "await_human", "rejected", "failed"}


# ---------------------------------------------------------------------------
# Each department through the real gates
# ---------------------------------------------------------------------------

async def test_creative_ops_spend_free_review_is_approved_and_executed() -> None:
    tool = MockTool(ProposalDraft(summary="refresh ad set", partition_key="creative:1"))
    result = await _run("creative_ops", tool, _evaluator())
    assert result.status == "executed" and result.decision.outcome == "approved"
    assert result.action_result == {"done": True}
    _assert_invariant(result, tool)


async def test_marketing_ops_first_external_launch_defers_to_human() -> None:
    tool = MockTool(ProposalDraft(summary="launch campaign", partition_key="campaign:1",
                                  spend_minor_units=50_000, currency="USD"))
    result = await _run("marketing_ops", tool, _evaluator(_capital(growth=100_000)))
    assert result.status == "deferred_to_human"
    assert result.decision.stage_failed_at == STAGE_HITL
    assert result.decision.hitl_trigger == HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH.value
    _assert_invariant(result, tool)


async def test_marketing_ops_repeat_launch_within_budget_executes() -> None:
    tool = MockTool(ProposalDraft(summary="relaunch", partition_key="campaign:1", spend_minor_units=50_000,
                                  currency="USD"))
    result = await _run("marketing_ops", tool, _evaluator(_capital(growth=100_000)), launch_history=_launched_before)
    assert result.status == "executed"
    _assert_invariant(result, tool)


async def test_marketing_ops_spend_over_ceiling_defers_at_capital() -> None:
    tool = MockTool(ProposalDraft(summary="big", partition_key="campaign:2", spend_minor_units=150_000,
                                  currency="USD"))
    result = await _run("marketing_ops", tool, _evaluator(_capital(growth=100_000)), launch_history=_launched_before)
    assert result.status == "deferred_to_human"
    assert result.decision.stage_failed_at == STAGE_CAPITAL
    assert any("spend_over_ceiling" in r for r in result.decision.reasons)
    _assert_invariant(result, tool)


async def test_missing_budget_ceiling_fails_closed_to_human() -> None:
    tool = MockTool(ProposalDraft(summary="x", partition_key="campaign:3", spend_minor_units=1,
                                  currency="USD"))
    result = await _run("marketing_ops", tool, _evaluator(_capital()), launch_history=_launched_before)
    assert result.status == "deferred_to_human"
    assert any("no_budget_ceiling_configured" in r for r in result.decision.reasons)
    _assert_invariant(result, tool)


@pytest.mark.parametrize("confidence,expected", [(0.3, "deferred_to_human"), (0.9, "executed")])
async def test_finance_risk_low_confidence_irreversible_reallocation(confidence: float, expected: str) -> None:
    tool = MockTool(ProposalDraft(summary="move budget", partition_key="budget:q4", spend_minor_units=20_000,
                                  currency="USD", metadata={"irreversible": True, "confidence": confidence}))
    result = await _run("finance_risk", tool, _evaluator(_capital(finance=100_000)))
    assert result.status == expected
    if expected == "deferred_to_human":
        assert result.decision.hitl_trigger == HumanInLoopTrigger.LOW_CONFIDENCE_IRREVERSIBLE.value
    _assert_invariant(result, tool)


async def test_market_intelligence_worker_cannot_propose_spend() -> None:
    tool = MockTool(ProposalDraft(summary="buy data", partition_key="mi:1", spend_minor_units=500, currency="USD"))
    result = await _run("market_intelligence", tool, _evaluator(_capital(growth=10_000)))
    assert result.status == "rejected"
    assert result.decision.stage_failed_at == STAGE_POLICY
    assert any("spend_requires_director" in r for r in result.decision.reasons)
    _assert_invariant(result, tool)


async def test_brand_safety_block_is_rejected_by_policy() -> None:
    tool = MockTool(ProposalDraft(summary="edgy", partition_key="creative:9", metadata={"brand_safety": "blocked"}))
    result = await _run("creative_ops", tool, _evaluator())
    assert result.status == "rejected"
    _assert_invariant(result, tool)


async def test_unregistered_agent_fails_closed() -> None:
    spec = DepartmentSpec(department_id="ghost", proposing_agent_id="ghost_agent",
                          action_kind="creative.review", capital_scope="growth")
    tool = MockTool()
    result = await run_department(spec, DepartmentDeps(evaluator=_evaluator(), tool=tool, authority=FakeAuthority()),
                                  org_id=ORG)
    assert result.status == "rejected" and result.decision.stage_failed_at == STAGE_AUTHORITY
    _assert_invariant(result, tool)


async def test_lower_authority_loses_conflict_on_same_partition() -> None:
    evaluator = _evaluator(_capital(growth=100_000))
    clock = _Clock()
    director = MockTool(ProposalDraft(summary="launch", partition_key="campaign:shared", spend_minor_units=1_000,
                                      currency="USD"))
    first = await run_department(SPECS["marketing_ops"], DepartmentDeps(
        evaluator=evaluator, tool=director, authority=FakeAuthority(), launch_history=_launched_before, now_fn=clock),
        org_id=ORG)
    assert first.status == "executed"
    manager = MockTool(ProposalDraft(summary="rework", partition_key="campaign:shared"))
    second = await run_department(SPECS["creative_ops"], DepartmentDeps(
        evaluator=evaluator, tool=manager, authority=FakeAuthority(), now_fn=clock), org_id=ORG)
    assert second.status == "rejected" and second.decision.stage_failed_at == STAGE_CONFLICT
    _assert_invariant(second, manager)


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------

async def test_plan_error_fails_without_evaluation_or_action() -> None:
    evaluator = _evaluator()
    tool = MockTool(plan_exc=RuntimeError("upstream down"))
    result = await _run("creative_ops", tool, evaluator)
    assert result.status == "failed" and "plan error" in (result.failure or "")
    assert evaluator.calls == 0 and tool.executions == []


async def test_evaluator_error_fails_without_action() -> None:
    tool = MockTool(ProposalDraft(summary="x", partition_key="creative:2"))
    result = await _run("creative_ops", tool, _evaluator(raise_exc=RuntimeError("ledger unreachable")))
    assert result.status == "failed" and "govern error" in (result.failure or "")
    assert tool.executions == []


async def test_execute_error_is_reported_as_failed() -> None:
    tool = MockTool(ProposalDraft(summary="x", partition_key="creative:3"), exec_exc=RuntimeError("boom"))
    result = await _run("creative_ops", tool, _evaluator())
    assert result.status == "failed" and "execute error" in (result.failure or "")
    assert len(tool.executions) == 1


# ---------------------------------------------------------------------------
# Chain
# ---------------------------------------------------------------------------

def _chain_tools() -> dict[str, MockTool]:
    return {
        "market_intelligence": MockTool(ProposalDraft(summary="trend scan", partition_key="mi:chain"),
                                        result={"trend": "eco bottles"}),
        "creative_ops": MockTool(ProposalDraft(summary="ad set", partition_key="creative:chain"),
                                 result={"creative_id": "c-1"}),
        "marketing_ops": MockTool(ProposalDraft(summary="launch", partition_key="campaign:chain",
                                                spend_minor_units=40_000, currency="USD"),
                                  result={"campaign_id": "cmp-1"}),
        "finance_risk": MockTool(ProposalDraft(summary="rebalance", partition_key="budget:chain",
                                               spend_minor_units=10_000, currency="USD"),
                                 result={"rebalanced": True}),
    }


async def test_chain_halts_at_first_deferral_and_never_acts_downstream() -> None:
    tools = _chain_tools()
    results = await run_department_chain(
        DEFAULT_DEPARTMENT_SPECS, tools, _evaluator(_capital(growth=100_000, finance=100_000)),
        authority=FakeAuthority(), org_id=ORG, payload={"niche": "kitchen"}, now_fn=_Clock(),
    )
    assert [(r.department_id, r.status) for r in results] == [
        ("market_intelligence", "executed"), ("creative_ops", "executed"), ("marketing_ops", "deferred_to_human"),
    ]
    assert tools["finance_risk"].plans == [] and tools["finance_risk"].executions == []
    assert tools["market_intelligence"].plans == [{"niche": "kitchen"}]
    assert tools["creative_ops"].plans == [{"trend": "eco bottles"}]  # output feeds the next department


async def test_chain_all_approved_runs_all_four() -> None:
    tools = _chain_tools()
    results = await run_department_chain(
        DEFAULT_DEPARTMENT_SPECS, tools, _evaluator(_capital(growth=100_000, finance=100_000)),
        authority=FakeAuthority(), launch_history=_launched_before, org_id=ORG, now_fn=_Clock(),
    )
    assert [r.status for r in results] == ["executed"] * 4
    assert tools["finance_risk"].plans == [{"campaign_id": "cmp-1"}]


async def test_chain_requires_a_tool_per_department() -> None:
    with pytest.raises(ValueError, match="no tool supplied"):
        await run_department_chain(DEFAULT_DEPARTMENT_SPECS, {}, _evaluator(), authority=FakeAuthority(), org_id=ORG)


# ---------------------------------------------------------------------------
# Independent-auditor findings (audit-D4 F1, F2, F4) and the M5 test gap
# ---------------------------------------------------------------------------

async def test_tool_cannot_declare_its_own_launch_history() -> None:
    """F1: a tool-supplied previously_launched is discarded; without history the
    launch is a first launch and defers to a human."""
    tool = MockTool(ProposalDraft(summary="launch", partition_key="campaign:f1", spend_minor_units=1_000,
                                  currency="USD", metadata={"previously_launched": True}))
    result = await _run("marketing_ops", tool, _evaluator(_capital(growth=100_000)))
    assert result.status == "deferred_to_human"
    assert result.decision.hitl_trigger == HumanInLoopTrigger.FIRST_EXTERNAL_LAUNCH.value
    _assert_invariant(result, tool)


@pytest.mark.parametrize("meta", [
    {"irreversible": True},                              # missing: evaluator would default to 1.0
    {"irreversible": True, "confidence": float("nan")},
    {"irreversible": True, "confidence": "0.99"},
    {"irreversible": True, "confidence": 7},
    {"irreversible": True, "confidence": True},
])
async def test_unusable_confidence_on_irreversible_draft_defers(meta: dict) -> None:
    """F1: missing / NaN / non-numeric / out-of-range confidence must not pass the gate."""
    tool = MockTool(ProposalDraft(summary="move budget", partition_key="budget:f1", spend_minor_units=1_000,
                                  currency="USD", metadata=meta))
    result = await _run("finance_risk", tool, _evaluator(_capital(finance=100_000)))
    assert result.status == "deferred_to_human"
    assert result.decision.hitl_trigger == HumanInLoopTrigger.LOW_CONFIDENCE_IRREVERSIBLE.value
    _assert_invariant(result, tool)


async def test_kill_switch_blocks_the_act_after_approval() -> None:
    """F2: live state is re-checked right before execute."""
    authority = FakeAuthority(deny="kill switch engaged")
    tool = MockTool(ProposalDraft(summary="refresh", partition_key="creative:f2"))
    result = await _run("creative_ops", tool, _evaluator(), authority=authority)
    assert result.decision.outcome == "approved"
    assert result.status == "blocked" and "kill switch engaged" in (result.failure or "")
    assert tool.executions == []
    assert authority.checks == [("creative_operations_manager", ORG)]


async def test_live_state_gate_not_consulted_without_approval() -> None:
    authority = FakeAuthority()
    tool = MockTool(ProposalDraft(summary="edgy", partition_key="creative:f2b", metadata={"brand_safety": "blocked"}))
    result = await _run("creative_ops", tool, _evaluator(), authority=authority)
    assert result.status == "rejected" and authority.checks == []


async def test_non_mapping_execute_result_is_outcome_unknown() -> None:
    """F4: the action may have happened; the run must still produce a record."""
    tool = MockTool(ProposalDraft(summary="x", partition_key="creative:f4"))
    tool.result = ["not", "a", "mapping"]  # type: ignore[assignment]
    result = await _run("creative_ops", tool, _evaluator())
    assert result.status == "failed" and "OUTCOME UNKNOWN" in (result.failure or "")
    assert len(tool.executions) == 1


async def test_unknown_outcome_never_acts() -> None:
    """M5: an outcome outside the vocabulary routes to failed, never to act."""

    class _OddEvaluator(SpyEvaluator):
        async def evaluate(self, proposal):  # type: ignore[no-untyped-def]
            real = await DecisionEvaluator.evaluate(self, proposal)
            return real.model_copy(update={"outcome": "maybe"})

    tool = MockTool(ProposalDraft(summary="x", partition_key="creative:m5"))
    result = await _run("creative_ops", tool, _OddEvaluator(registry=MVP_REGISTRY, capital=_capital()))
    assert result.status == "failed" and tool.executions == []
