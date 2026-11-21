"""The governed department flow: plan -> govern -> (act | await_human | rejected).

Shape (explicit, inspectable nodes, same discipline as
orchestrator/workflows/creative_workflow.py):

    START -> plan -> govern -+-> act ---------> END     (outcome == "approved")
                    |        +-> await_human -> END     (outcome == "deferred_to_human")
                    |        +-> rejected ----> END     (outcome == "rejected")
                    +-> failed -----------------> END   (plan/govern raised, unknown outcome)

THE INVARIANT: the department tool's `execute` is reachable only through the
`act` node, and `act` is reachable only on an `approved` verdict from the real
`DecisionEvaluator`. Every other path — including any exception in planning or
evaluation — ends without acting (fail closed).

TWO GATES THE TOOL DOES NOT CONTROL
- Governance signals. The tool that will `execute` must not be the one that
  tells the HITL stage whether a launch is a first launch: `previously_launched`
  is stripped from the draft and set ONLY from `DepartmentDeps.launch_history`
  (absent -> False -> every external launch is a first launch). `confidence`
  that is missing (on an irreversible draft), non-numeric, NaN or outside
  [0, 1] becomes 0.0, so the low-confidence-irreversible trigger fires instead
  of silently passing (the evaluator defaults a missing value to 1.0).
- Live state. Immediately before `execute`, `DepartmentDeps.authority
  .assert_active(agent_id, org_id)` (GovernanceAuthority, governance/authority.py)
  re-checks suspension and the kill switch; any exception there blocks the act.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal, Mapping, Protocol, Sequence, TypedDict
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph

from ...decision_engine.evaluator import DecisionEvaluator
from ...decision_engine.events import DecisionProposal, DecisionResult
from .contracts import DepartmentSpec, DepartmentTool, ProposalDraft

RunStatus = Literal["executed", "deferred_to_human", "rejected", "blocked", "failed"]

#: Metadata keys only the flow may set; a tool-supplied value is discarded.
_FLOW_OWNED_SIGNALS = frozenset({"previously_launched"})


class ActiveAgentGate(Protocol):
    """The live-state check run right before `execute` (GovernanceAuthority
    satisfies it). Must RAISE when the agent is suspended or kill-switched."""

    async def assert_active(self, agent_id: str, org_id: str) -> None: ...


LaunchHistory = Callable[[str, DepartmentSpec, ProposalDraft], Awaitable[bool]]


class DepartmentState(TypedDict, total=False):
    org_id: str
    correlation_id: UUID
    payload: Mapping[str, Any]
    draft: ProposalDraft | None
    decision: DecisionResult | None
    action_result: Mapping[str, Any] | None
    status: RunStatus
    failure: str | None


@dataclass(slots=True)
class DepartmentDeps:
    evaluator: DecisionEvaluator
    tool: DepartmentTool
    #: Required, no default: a flow without a live-state gate cannot act.
    authority: ActiveAgentGate
    #: Whether this org has launched this department's action before. None
    #: means "unknown", which is treated as "never" (fail closed to HITL).
    launch_history: LaunchHistory | None = None
    now_fn: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))
    id_fn: Callable[[], UUID] = uuid4


@dataclass(frozen=True, slots=True)
class DepartmentRunResult:
    department_id: str
    status: RunStatus
    draft: ProposalDraft | None
    decision: DecisionResult | None
    action_result: Mapping[str, Any] | None
    failure: str | None


def _confidence(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if math.isfinite(f) and 0.0 <= f <= 1.0 else None


async def _governed_metadata(
    spec: DepartmentSpec, deps: DepartmentDeps, state: DepartmentState, draft: ProposalDraft
) -> dict[str, Any]:
    meta = {k: v for k, v in draft.metadata.items() if k not in _FLOW_OWNED_SIGNALS}
    launched = False
    if deps.launch_history is not None:
        launched = await deps.launch_history(state["org_id"], spec, draft) is True
    meta["previously_launched"] = launched
    if "confidence" in meta or meta.get("irreversible"):
        conf = _confidence(meta.get("confidence"))
        meta["confidence"] = 0.0 if conf is None else conf
    return meta


def _proposal(
    spec: DepartmentSpec,
    deps: DepartmentDeps,
    state: DepartmentState,
    draft: ProposalDraft,
    metadata: dict[str, Any],
) -> DecisionProposal:
    pid = deps.id_fn()
    return DecisionProposal(
        proposal_id=pid,
        correlation_id=state["correlation_id"],
        partition_key=draft.partition_key,
        org_id=state["org_id"],
        department=spec.department_id,
        proposing_agent_id=spec.proposing_agent_id,
        action_kind=spec.action_kind,
        requires_external_launch=spec.requires_external_launch,
        spend_minor_units=draft.spend_minor_units,
        currency=draft.currency,
        capital_scope=spec.capital_scope,
        occurred_at=deps.now_fn(),
        source_event_id=pid,
        source_type=f"department.{spec.department_id}",
        metadata=metadata,
    )


def build_department_graph(spec: DepartmentSpec, deps: DepartmentDeps) -> Any:
    """Compile one department's governed graph (typed Any: langgraph ships no
    py.typed marker, see pyproject [tool.mypy] overrides)."""

    async def plan(state: DepartmentState) -> DepartmentState:
        try:
            draft = await deps.tool.plan(org_id=state["org_id"], payload=state.get("payload", {}))
        except Exception as exc:  # noqa: BLE001 — surfaced as a governed failure
            return {"failure": f"plan error: {type(exc).__name__}: {exc}"}
        return {"draft": draft}

    async def govern(state: DepartmentState) -> DepartmentState:
        draft = state.get("draft")
        if draft is None:
            return {"failure": "govern reached without a draft"}
        try:
            metadata = await _governed_metadata(spec, deps, state, draft)
            decision = await deps.evaluator.evaluate(_proposal(spec, deps, state, draft, metadata))
        except Exception as exc:  # noqa: BLE001 — evaluation failure never acts
            return {"failure": f"govern error: {type(exc).__name__}: {exc}"}
        return {"decision": decision}

    async def act(state: DepartmentState) -> DepartmentState:
        draft, decision = state.get("draft"), state.get("decision")
        # Defence in depth: the router already guarantees this.
        if draft is None or decision is None or decision.outcome != "approved":
            return {"status": "failed", "failure": "act reached without an approved decision"}
        try:
            await deps.authority.assert_active(spec.proposing_agent_id, state["org_id"])
        except Exception as exc:  # noqa: BLE001 — any doubt about live state blocks the act
            return {"status": "blocked", "failure": f"live-state gate: {type(exc).__name__}: {exc}"}
        try:
            result = await deps.tool.execute(org_id=state["org_id"], draft=draft, decision=decision)
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "failure": f"execute error: {type(exc).__name__}: {exc}"}
        if not isinstance(result, Mapping):
            # The action may have happened; only its report is unusable.
            return {
                "status": "failed",
                "failure": "OUTCOME UNKNOWN: execute returned a non-mapping result "
                f"({type(result).__name__}); verify before re-running",
            }
        return {"status": "executed", "action_result": dict(result)}

    async def await_human(state: DepartmentState) -> DepartmentState:
        return {"status": "deferred_to_human"}

    async def rejected(state: DepartmentState) -> DepartmentState:
        return {"status": "rejected"}

    async def failed(state: DepartmentState) -> DepartmentState:
        return {"status": "failed"}

    def route_after_plan(state: DepartmentState) -> str:
        return "failed" if state.get("failure") else "govern"

    def route_after_govern(state: DepartmentState) -> str:
        if state.get("failure"):
            return "failed"
        decision = state.get("decision")
        outcome = decision.outcome if decision is not None else None
        if outcome == "approved":
            return "act"
        if outcome == "deferred_to_human":
            return "await_human"
        if outcome == "rejected":
            return "rejected"
        return "failed"  # unknown outcome: never act

    graph = StateGraph(DepartmentState)
    for name, node in (
        ("plan", plan),
        ("govern", govern),
        ("act", act),
        ("await_human", await_human),
        ("rejected", rejected),
        ("failed", failed),
    ):
        graph.add_node(name, node)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", route_after_plan, {"govern": "govern", "failed": "failed"})
    graph.add_conditional_edges(
        "govern",
        route_after_govern,
        {"act": "act", "await_human": "await_human", "rejected": "rejected", "failed": "failed"},
    )
    for terminal in ("act", "await_human", "rejected", "failed"):
        graph.add_edge(terminal, END)
    return graph.compile()


async def run_department(
    spec: DepartmentSpec,
    deps: DepartmentDeps,
    *,
    org_id: str,
    payload: Mapping[str, Any] | None = None,
    correlation_id: UUID | None = None,
) -> DepartmentRunResult:
    final = await build_department_graph(spec, deps).ainvoke(
        {
            "org_id": org_id,
            "correlation_id": correlation_id or deps.id_fn(),
            "payload": dict(payload or {}),
        }
    )
    return DepartmentRunResult(
        department_id=spec.department_id,
        status=final.get("status", "failed"),
        draft=final.get("draft"),
        decision=final.get("decision"),
        action_result=final.get("action_result"),
        failure=final.get("failure"),
    )


async def run_department_chain(
    specs: Sequence[DepartmentSpec],
    tools: Mapping[str, DepartmentTool],
    evaluator: DecisionEvaluator,
    *,
    authority: ActiveAgentGate,
    launch_history: LaunchHistory | None = None,
    org_id: str,
    payload: Mapping[str, Any] | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> list[DepartmentRunResult]:
    """Run departments in order under ONE correlation id, feeding each executed
    department's `action_result` to the next as its payload. Stops at the first
    department that did not execute: a later department never acts on the
    output of one that was deferred, rejected or failed.
    """
    missing = [s.department_id for s in specs if s.department_id not in tools]
    if missing:
        raise ValueError(f"no tool supplied for departments: {missing}")
    correlation_id = uuid4()
    results: list[DepartmentRunResult] = []
    current: Mapping[str, Any] = dict(payload or {})
    for spec in specs:
        deps = DepartmentDeps(
            evaluator=evaluator,
            tool=tools[spec.department_id],
            authority=authority,
            launch_history=launch_history,
        )
        if now_fn is not None:
            deps.now_fn = now_fn
        result = await run_department(
            spec, deps, org_id=org_id, payload=current, correlation_id=correlation_id
        )
        results.append(result)
        if result.status != "executed":
            break
        current = result.action_result or {}
    return results
