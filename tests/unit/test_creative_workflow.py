"""Unit test for the creative workflow's governance-checkpoint budget wiring.

Pins that the LangGraph ``governance_checkpoint`` node feeds the *real* per-step
token cost into the canonical ordered validation pipeline. Before the fix it
passed a hardcoded ``requested_token_cost=0``, which made the BUDGET stage a
structural no-op regardless of the contract's configured budget.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

from skylize.app.orchestrator.runner import StubAgentRunner
from skylize.app.orchestrator.workflows.creative_workflow import (
    GraphDeps,
    build_creative_graph,
)
from skylize.contracts.base import GovernanceToken
from skylize.contracts.registry import MVP_REGISTRY
from skylize.contracts.token import TokenValidationResult

ORG = "org_test"


def _token_for(contract) -> GovernanceToken:
    """A structurally-complete token for the graph state. Its signature is never
    verified here because the validator is spied — the LangGraph checkpointer
    only needs it to be serializable (a real Pydantic model), which a MagicMock
    is not."""
    now = datetime.now(timezone.utc)
    return GovernanceToken(
        token_id=uuid4(),
        agent_id=contract.agent_id,
        authority_level=contract.authority_level,
        department=contract.department,
        delegation_chain=[contract.agent_id],
        scope=[grant.tool_id for grant in contract.allowed_tools],
        max_token_budget=contract.max_token_budget,
        max_execution_time_seconds=contract.max_execution_time_seconds,
        issued_at=now,
        expires_at=now + timedelta(minutes=10),
        nonce=uuid4().hex,
        signature="",
    )


async def test_governance_checkpoint_feeds_real_step_cost(monkeypatch) -> None:
    contract = MVP_REGISTRY.resolve("hook_generator_agent")  # single-shot, budget 8000

    captured: dict = {}

    def _spy(**kwargs):
        captured.update(kwargs)
        return TokenValidationResult.ok()

    # Replace the canonical validator with a spy so we can assert exactly what
    # the checkpoint hands it; returning ok() lets the graph proceed to output.
    monkeypatch.setattr(
        "skylize.app.orchestrator.workflows.creative_workflow.validate_tool_call",
        _spy,
    )

    # public_key / live_state are consumed only by the (spied) validator, so a
    # MagicMock suffices there; StubAgentRunner runs the agent_step
    # deterministically with no LLM.
    deps = GraphDeps(
        runner=StubAgentRunner(),
        public_key=MagicMock(),
        live_state_for=lambda _org: MagicMock(),
    )
    graph = build_creative_graph(deps)

    correlation_id = uuid4()
    state = {
        "org_id": ORG,
        "correlation_id": correlation_id,
        "agent_id": "hook_generator_agent",
        "contract": contract,
        "token": _token_for(contract),
        "input_payload": {
            "brand_name": "TestBrand",
            "product_description": "A revolutionary widget",
            "target_audience": "startup founders",
            "count": 3,
        },
        "output": None,
        "run_meta": None,
        "failure": None,
        "failed_stage": None,
    }
    final = await graph.ainvoke(
        state, config={"configurable": {"thread_id": str(correlation_id)}}
    )

    # The checkpoint passed the real per-step ceiling, not the pre-fix 0.
    assert captured["requested_token_cost"] == min(contract.max_token_budget // 2, 4096)
    assert captured["requested_token_cost"] != 0
    # Valid gate -> the graph proceeded through agent_step to output.
    assert final.get("failure") is None
    assert final.get("output") is not None
