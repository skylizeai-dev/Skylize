"""Attribution parity across the four live LLM call paths.

Proves that `correlation_id` and `agent_id` arrive INTACT at the gateway
adapter on every live path, and that on Paths 1 and 2 the correlation_id
reaching the adapter is the SAME run_id AgentExecutionService minted — not a
fresh uuid4 invented at the call site.

  Path 1  AgentExecutionService single-shot   -> generate
  Path 2  AgentExecutionService tool loop     -> generate_with_tools
  Path 3  Orchestrator -> LLMStepRunner       -> generate
  Path 4  WorkflowActivities -> LLMJudge      -> generate
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from skylize.adapters.llm.gateway import (
    LLMContentBlock,
    LLMGenerateRequest,
    LLMGenerateResponse,
    LLMGenerateWithToolsRequest,
    LLMUsage,
)
from skylize.app.agents.execution import AgentExecutionService
from skylize.app.audit.service import AuditService
from skylize.app.governance import GovernanceAuthority
from skylize.app.orchestrator.orchestrator import Orchestrator
from skylize.app.orchestrator.runner import LLMStepRunner
from skylize.app.orchestrator.temporal import LLMJudge
from skylize.app.orchestrator.temporal.activities import (
    JudgeRequest,
    RunContext,
    WorkflowActivities,
)
from skylize.config import Settings
from skylize.contracts.base import AgentContract, FailureMode, ToolGrant
from skylize.contracts.registry import MVP_REGISTRY, AgentRegistry
from skylize.dal.memory import InMemoryAuditRepository, InMemoryGovernanceRepository
from skylize.events.memory_bus import InMemoryEventBus
from skylize.tools.builtin import build_builtin_tools
from skylize.tools.builtin.memory_recall import NullMemoryRecallPort
from skylize.tools.proxy import ToolProxy
from skylize.tools.registry import ToolRegistry

ORG = "org_test"

_INPUT = {
    "brand_name": "TestBrand",
    "product_description": "A revolutionary widget",
    "target_audience": "startup founders",
}

_TOOL_LOOP_CONTRACT = AgentContract(
    agent_id="test_tool_loop_agent",
    agent_role="Hook Generator (attribution test variant)",
    authority_level="worker",
    department="creative",
    input_schema="skylize.schemas.agents.creative.HookGeneratorExecuteIn",
    output_schema="skylize.schemas.agents.creative.HookGeneratorExecuteOut",
    allowed_tools=[
        ToolGrant(tool_id="llm.generate", purpose="generate hooks"),
        ToolGrant(tool_id="memory.search", purpose="recall past hooks"),
    ],
    invocable_tools=["memory.search"],
    max_tool_iterations=3,
    max_token_budget=8_000,
    max_execution_time_seconds=60,
    escalation_path=["copy_director", "vp_creative", "cmo", "ceo", "human_owner"],
    failure_mode=FailureMode.FALLBACK_DEGRADED,
    memory_read_access=["creative:copy:hooks"],
    memory_write_access=[],
)


class _CapturingGateway:
    """Fake gateway that records every request and returns valid hooks JSON."""

    def __init__(self) -> None:
        self.generate_requests: list[LLMGenerateRequest] = []
        self.tools_requests: list[LLMGenerateWithToolsRequest] = []

    async def generate(self, request: LLMGenerateRequest) -> LLMGenerateResponse:
        self.generate_requests.append(request)
        text = json.dumps({"hooks": ["Hook A", "Hook B"]})
        return LLMGenerateResponse(
            text=text,
            provider="fake",
            concrete_model="fake-1",
            usage=LLMUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
        )

    def generate_sync(self, request: LLMGenerateRequest) -> LLMGenerateResponse:
        raise NotImplementedError

    async def generate_with_tools(
        self, request: LLMGenerateWithToolsRequest, tools: Any
    ) -> LLMGenerateResponse:
        self.tools_requests.append(request)
        text = json.dumps({"hooks": ["Hook A", "Hook B"]})
        return LLMGenerateResponse(
            text=text,
            provider="fake",
            concrete_model="fake-1",
            usage=LLMUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            stop_reason="end_turn",
            content=[LLMContentBlock(kind="text", text=text)],
        )


def _governance(contract: AgentContract):
    bus = InMemoryEventBus()
    audit = AuditService(bus, InMemoryAuditRepository())
    authority = GovernanceAuthority.build(
        repo=InMemoryGovernanceRepository(), audit=audit, bus=bus,
        registry=AgentRegistry([contract]), settings=Settings(backend="memory"),
    )
    return bus, audit, authority


def _deliverable_mock() -> MagicMock:
    svc = MagicMock()
    row = MagicMock()
    row.id = uuid4()
    svc.create_deliverable = AsyncMock(return_value=row)
    return svc


# ---------------------------------------------------------------------------
# Path 1 — single-shot execute() -> generate
# ---------------------------------------------------------------------------


async def test_path1_single_shot_threads_minted_run_id_and_agent_id() -> None:
    contract = MVP_REGISTRY.resolve("hook_generator_agent")
    bus, audit, authority = _governance(contract)
    gw = _CapturingGateway()
    service = AgentExecutionService(
        registry=AgentRegistry([contract]), llm=gw, deliverables=_deliverable_mock(),
        authority=authority, audit=audit,
    )

    minted = UUID("00000000-0000-4000-8000-00000000c0de")
    with patch("skylize.app.agents.execution.uuid4", return_value=minted):
        await service.execute(
            org_id=ORG, agent_id="hook_generator_agent", input_data=_INPUT, user_id="u1",
        )

    assert len(gw.generate_requests) == 1
    req = gw.generate_requests[0]
    # The SAME run_id the service minted — not a fresh uuid4 at the call site.
    assert req.correlation_id == minted
    assert req.agent_id == "hook_generator_agent"


# ---------------------------------------------------------------------------
# Path 2 — tool loop execute() -> generate_with_tools
# ---------------------------------------------------------------------------


async def test_path2_tool_loop_threads_minted_run_id_and_token_agent_id() -> None:
    bus, audit, authority = _governance(_TOOL_LOOP_CONTRACT)
    tool_registry = ToolRegistry(build_builtin_tools(NullMemoryRecallPort()))
    proxy = ToolProxy(
        registry=tool_registry, audit=audit,
        public_key=authority.public_key, live_state_for=authority.live_state_checker,
    )
    gw = _CapturingGateway()
    service = AgentExecutionService(
        registry=AgentRegistry([_TOOL_LOOP_CONTRACT]), llm=gw,
        deliverables=_deliverable_mock(), tools=proxy, authority=authority, audit=audit,
    )

    minted = UUID("00000000-0000-4000-8000-0000000010c0")
    with patch("skylize.app.agents.execution.uuid4", return_value=minted):
        await service.execute(
            org_id=ORG, agent_id="test_tool_loop_agent", input_data=_INPUT, user_id="u1",
        )

    assert len(gw.tools_requests) == 1
    req = gw.tools_requests[0]
    # The SAME run_id the service minted, and the minted token's agent_id.
    assert req.correlation_id == minted
    assert req.agent_id == "test_tool_loop_agent"


# ---------------------------------------------------------------------------
# Path 3 — Orchestrator -> LLMStepRunner -> generate
# ---------------------------------------------------------------------------


async def test_path3_orchestrator_threads_correlation_id_to_adapter() -> None:
    contract = MVP_REGISTRY.resolve("hook_generator_agent")
    bus, audit, authority = _governance(contract)
    gw = _CapturingGateway()
    orchestrator = Orchestrator(
        registry=AgentRegistry([contract]), authority=authority, audit=audit,
        bus=bus, runner=LLMStepRunner(gw),
    )

    correlation_id = uuid4()
    result = await orchestrator.invoke(
        "hook_generator_agent", dict(_INPUT, count=2),
        org_id=ORG, correlation_id=correlation_id,
    )

    assert result.status == "completed", result.reason
    assert len(gw.generate_requests) == 1
    req = gw.generate_requests[0]
    # The id handed to the Orchestrator, intact through graph state + runner.
    assert req.correlation_id == correlation_id
    assert req.agent_id == "hook_generator_agent"
    assert req.governance_token_id == result.token_id


# ---------------------------------------------------------------------------
# Path 4 — WorkflowActivities -> LLMJudge -> generate
# ---------------------------------------------------------------------------


async def test_path4_judge_threads_run_context_correlation_and_agent_id() -> None:
    gw = _CapturingGateway()
    acts = WorkflowActivities(
        repo=None, builder=None, judge=LLMJudge(gw), minter=None  # type: ignore[arg-type]
    )
    correlation_id = uuid4()
    ctx = RunContext(
        org_id=ORG, run_id=str(uuid4()), workflow_id="wf_demo",
        correlation_id=str(correlation_id), thread_id="t1", triggered_by="test",
        governance_token_id=uuid4(),
    )

    await acts.run_judge_verification(
        JudgeRequest(
            ctx=ctx, node_name="draft_copy", output={"copy": "fine"},
            success_criteria={"tone": "neutral"}, agent_id="draft_copy_agent",
        )
    )

    assert len(gw.generate_requests) == 1
    req = gw.generate_requests[0]
    assert req.correlation_id == correlation_id
    assert req.agent_id == "draft_copy_agent"
    assert req.org_id == ORG


async def test_path4_judge_passes_distinct_governance_token_and_correlation_id() -> None:
    """The judge egress must carry the run's governance token id as
    ``governance_token_id`` and the run correlation id as ``correlation_id`` — two
    DISTINCT values. Aliasing them (the pre-fix bug at activities.py, where both
    were set to ctx.correlation_id) would make the ai_cost_ledger row on the judge
    path record run_id == correlation_id, violating cost_ledger.py:114.
    """
    gw = _CapturingGateway()
    acts = WorkflowActivities(
        repo=None, builder=None, judge=LLMJudge(gw), minter=None  # type: ignore[arg-type]
    )
    governance_token_id = uuid4()
    correlation_id = uuid4()
    assert governance_token_id != correlation_id
    ctx = RunContext(
        org_id=ORG, run_id=str(uuid4()), workflow_id="wf_demo",
        correlation_id=str(correlation_id), thread_id="t1", triggered_by="test",
        governance_token_id=governance_token_id,
    )

    await acts.run_judge_verification(
        JudgeRequest(
            ctx=ctx, node_name="draft_copy", output={"copy": "fine"},
            success_criteria={"tone": "neutral"}, agent_id="draft_copy_agent",
        )
    )

    assert len(gw.generate_requests) == 1
    req = gw.generate_requests[0]
    # The two attribution ids reaching the adapter are the two distinct RunContext
    # ids — governance token id for the ledger run key, correlation id for tracing.
    assert req.governance_token_id == governance_token_id
    assert req.correlation_id == correlation_id
    assert req.governance_token_id != req.correlation_id
