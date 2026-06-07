"""
The creative-run LangGraph state machine.

Nodes are explicit and inspectable (system_architecture.md §5.2): a governance
checkpoint, the agent step, and an emit node — with a failure branch. Control
flow is deterministic; only the agent step reasons. State is checkpointed so the
graph can pause/resume (the HITL pause node is added in Sprint 3).

The graph is built behind the Orchestrator facade with its dependencies injected
via `GraphDeps`, so neither LangGraph nor the runner leaks into agent contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TypedDict
from uuid import UUID

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from ....contracts.base import AgentContract, GovernanceToken
from ....contracts.token import validate_tool_call
from ..runner import AgentRunner


class WorkflowState(TypedDict, total=False):
    org_id: str
    correlation_id: UUID
    agent_id: str
    contract: AgentContract
    token: GovernanceToken
    input_payload: dict[str, Any]
    output: dict[str, Any] | None
    failure: str | None
    failed_stage: str | None


@dataclass(slots=True)
class GraphDeps:
    runner: AgentRunner
    public_key: Any  # EllipticCurvePublicKey
    live_state_for: Any  # Callable[[org_id], LiveStateChecker]


def build_creative_graph(deps: GraphDeps) -> Any:
    """Compile the creative workflow graph with an in-memory checkpointer.

    Returns a LangGraph ``CompiledStateGraph``; typed as Any because langgraph
    ships no py.typed marker (see [tool.mypy] overrides).
    """

    async def governance_checkpoint(state: WorkflowState) -> WorkflowState:
        # Re-validate live state at this point: a kill switch may have fired or a
        # token been revoked since minting. Validate the agent's primary tool
        # (llm.generate) through the canonical ordered pipeline.
        token = state["token"]
        contract = state["contract"]
        checker = deps.live_state_for(state["org_id"])
        allowed = {t.tool_id for t in contract.allowed_tools}
        primary_tool = "llm.generate" if "llm.generate" in allowed else next(iter(allowed))
        result = validate_tool_call(
            token=token,
            public_key=deps.public_key,
            requested_tool_id=primary_tool,
            contract_allowed_tool_ids=allowed,
            requested_token_cost=0,
            tokens_used_so_far=0,
            live_state=checker,
            now=datetime.now(timezone.utc),
        )
        if not result.is_valid:
            return {
                "failure": result.reason,
                "failed_stage": result.failed_stage.value if result.failed_stage else None,
            }
        return {}

    async def agent_step(state: WorkflowState) -> WorkflowState:
        try:
            output = await deps.runner.run(
                contract=state["contract"],
                input_payload=state["input_payload"],
                token=state["token"],
            )
            return {"output": output}
        except Exception as exc:  # noqa: BLE001 — surfaced as a governed failure
            return {"failure": f"agent_step error: {exc}", "failed_stage": "agent_step"}

    async def emit(state: WorkflowState) -> WorkflowState:
        return {}  # event publication + audit happen in the Orchestrator facade

    async def handle_failure(state: WorkflowState) -> WorkflowState:
        return {}

    def route_after_governance(state: WorkflowState) -> str:
        return "handle_failure" if state.get("failure") else "agent_step"

    def route_after_agent(state: WorkflowState) -> str:
        return "handle_failure" if state.get("failure") else "emit"

    graph = StateGraph(WorkflowState)
    graph.add_node("governance_checkpoint", governance_checkpoint)
    graph.add_node("agent_step", agent_step)
    graph.add_node("emit", emit)
    graph.add_node("handle_failure", handle_failure)

    graph.set_entry_point("governance_checkpoint")
    graph.add_conditional_edges(
        "governance_checkpoint", route_after_governance,
        {"agent_step": "agent_step", "handle_failure": "handle_failure"},
    )
    graph.add_conditional_edges(
        "agent_step", route_after_agent,
        {"emit": "emit", "handle_failure": "handle_failure"},
    )
    graph.add_edge("emit", END)
    graph.add_edge("handle_failure", END)

    return graph.compile(checkpointer=MemorySaver())
