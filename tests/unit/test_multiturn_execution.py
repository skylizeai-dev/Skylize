"""Unit tests for AgentExecutionService's multi-turn tool-calling loop.

Covers: full loop (prompt -> tool_use -> tool_result -> final answer),
max_tool_iterations enforcement (governance escalation, not silent
truncation), backward compatibility of the single-shot path for contracts
with empty `invocable_tools`, and demo mode simulating one real tool call.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skylize.adapters.llm.demo_adapter import DemoLLMAdapter
from skylize.adapters.llm.gateway import (
    LLMContentBlock,
    LLMGenerateResponse,
    LLMGenerateWithToolsRequest,
    LLMMessage,
    LLMUsage,
    TokenBudgetExceeded,
)
from skylize.app.agents.execution import (
    AgentExecutionService,
    AgentToolLoopExceeded,
)
from skylize.app.audit.service import AuditService
from skylize.app.governance import GovernanceAuthority
from skylize.config import Settings
from skylize.contracts.base import AgentContract, FailureMode, ToolGrant
from skylize.contracts.registry import AgentRegistry, MVP_REGISTRY
from skylize.dal.memory import InMemoryAuditRepository, InMemoryGovernanceRepository
from skylize.events.memory_bus import InMemoryEventBus
from skylize.tools.builtin import build_builtin_tools
from skylize.tools.builtin.memory_recall import NullMemoryRecallPort
from skylize.tools.proxy import ToolProxy
from skylize.tools.registry import ToolRegistry

ORG = "org_test"

_TOOL_LOOP_CONTRACT = AgentContract(
    agent_id="test_tool_loop_agent",
    agent_role="Hook Generator (tool-loop test variant)",
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

_INPUT = {
    "brand_name": "TestBrand",
    "product_description": "A revolutionary widget",
    "target_audience": "startup founders",
}


def _harness(contract: AgentContract = _TOOL_LOOP_CONTRACT):
    bus = InMemoryEventBus()
    audit = AuditService(bus, InMemoryAuditRepository())
    authority = GovernanceAuthority.build(
        repo=InMemoryGovernanceRepository(), audit=audit, bus=bus,
        registry=AgentRegistry([contract]), settings=Settings(backend="memory"),
    )
    tool_registry = ToolRegistry(build_builtin_tools(NullMemoryRecallPort()))
    proxy = ToolProxy(
        registry=tool_registry, audit=audit,
        public_key=authority.public_key, live_state_for=authority.live_state_checker,
    )
    return authority, audit, bus, proxy


def _deliverable_mock():
    svc = MagicMock()
    row = MagicMock()
    row.id = uuid4()
    row.agent_id = "test_tool_loop_agent"
    svc.create_deliverable = AsyncMock(return_value=row)
    return svc, row


class _ScriptedLLM:
    """Returns queued responses in order; asserts generate() is never called."""

    def __init__(self, turns: list[LLMGenerateResponse]) -> None:
        self._turns = list(turns)
        self.calls: list[LLMGenerateWithToolsRequest] = []

    async def generate(self, request):  # noqa: D401 — should not be reached
        raise AssertionError("single-shot generate() must not be called on the tool-loop path")

    def generate_sync(self, request):
        raise NotImplementedError

    async def generate_with_tools(self, request, tools):
        self.calls.append(request)
        return self._turns.pop(0)

    async def generate_structured(self, request, schema, *, correlation_id):
        raise NotImplementedError


def _tool_use_response(query: str = "high-performing hooks") -> LLMGenerateResponse:
    return LLMGenerateResponse(
        text="", provider="test", concrete_model="test-1",
        usage=LLMUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30),
        stop_reason="tool_use",
        content=[LLMContentBlock(
            kind="tool_use", tool_use_id="call_1", tool_name="memory.search",
            tool_input={"query": query},
        )],
    )


def _final_response(hooks: list[str]) -> LLMGenerateResponse:
    text = json.dumps({"hooks": hooks})
    return LLMGenerateResponse(
        text=text, provider="test", concrete_model="test-1",
        usage=LLMUsage(prompt_tokens=15, completion_tokens=25, total_tokens=40),
        stop_reason="end_turn",
        content=[LLMContentBlock(kind="text", text=text)],
    )


# ── Full loop: prompt -> tool_use -> tool_result -> final answer ───────────

async def test_full_tool_loop_produces_deliverable() -> None:
    authority, audit, bus, proxy = _harness()
    llm = _ScriptedLLM([_tool_use_response(), _final_response(["Hook A", "Hook B"])])
    deliverables, row = _deliverable_mock()

    service = AgentExecutionService(
        registry=AgentRegistry([_TOOL_LOOP_CONTRACT]), llm=llm, deliverables=deliverables,
        tools=proxy, authority=authority, audit=audit,
    )
    result = await service.execute(
        org_id=ORG, agent_id="test_tool_loop_agent", input_data=_INPUT, user_id="u1",
    )
    assert result is row
    assert len(llm.calls) == 2

    # Second call's transcript carries the assistant tool_use turn + the
    # tool_result turn produced by the ToolProxy dispatch.
    second_call_messages = llm.calls[1].messages
    assert second_call_messages[1].role == "assistant"
    assert second_call_messages[1].content[0].kind == "tool_use"
    assert second_call_messages[2].role == "user"
    tool_result_block = second_call_messages[2].content[0]
    assert tool_result_block.kind == "tool_result"
    assert tool_result_block.tool_use_id == "call_1"
    assert json.loads(tool_result_block.tool_output) == {"hits": []}

    content_markdown = deliverables.create_deliverable.call_args.kwargs["content_markdown"]
    assert "Hook A" in content_markdown and "Hook B" in content_markdown


# ── max_tool_iterations enforced: governance escalation, not silent cutoff ─

async def test_exceeding_max_tool_iterations_raises_and_escalates() -> None:
    looping_contract = _TOOL_LOOP_CONTRACT.model_copy(update={"max_tool_iterations": 2})
    authority, audit, bus, proxy = _harness(looping_contract)
    # The model asks to call the tool forever — never finalizes.
    llm = _ScriptedLLM([_tool_use_response(), _tool_use_response()])
    deliverables, _ = _deliverable_mock()

    service = AgentExecutionService(
        registry=AgentRegistry([looping_contract]), llm=llm, deliverables=deliverables,
        tools=proxy, authority=authority, audit=audit,
    )
    with pytest.raises(AgentToolLoopExceeded):
        await service.execute(
            org_id=ORG, agent_id="test_tool_loop_agent", input_data=_INPUT, user_id="u1",
        )
    assert len(llm.calls) == 2  # exactly max_tool_iterations calls, no more

    recorded = bus.published_of_type("audit.action_recorded")
    escalations = [e for e in recorded if e.payload.action_type == "governance.tool_loop_exceeded"]
    assert len(escalations) == 1
    assert escalations[0].payload.result == "escalated"
    deliverables.create_deliverable.assert_not_called()


# ── Budget ceiling enforced mid-loop: refuse before the next LLM egress ────

async def test_budget_ceiling_trips_before_next_llm_turn() -> None:
    # Small budget so the running token ledger crosses it mid-loop. Per-turn
    # ceiling = min(budget // 2, 4096) = 3000; the first turn burns 3500 real
    # tokens, so the pre-egress check on the SECOND turn sees 3500 + 3000 > 6000
    # and refuses before the call reaches the provider. Proves the BUDGET stage
    # can now actually fire (was structurally dead with the hardcoded 0/0).
    budget_contract = _TOOL_LOOP_CONTRACT.model_copy(
        update={"max_token_budget": 6_000, "max_tool_iterations": 5}
    )
    authority, audit, bus, proxy = _harness(budget_contract)

    heavy_turn = LLMGenerateResponse(
        text="", provider="test", concrete_model="test-1",
        usage=LLMUsage(prompt_tokens=3000, completion_tokens=500, total_tokens=3500),
        stop_reason="tool_use",
        content=[LLMContentBlock(
            kind="tool_use", tool_use_id="call_1", tool_name="memory.search",
            tool_input={"query": "hooks"},
        )],
    )
    llm = _ScriptedLLM([heavy_turn])  # only ONE turn scripted — the 2nd never runs
    deliverables, _ = _deliverable_mock()

    service = AgentExecutionService(
        registry=AgentRegistry([budget_contract]), llm=llm, deliverables=deliverables,
        tools=proxy, authority=authority, audit=audit,
    )
    with pytest.raises(TokenBudgetExceeded):
        await service.execute(
            org_id=ORG, agent_id="test_tool_loop_agent", input_data=_INPUT, user_id="u1",
        )
    # Tripped on the 2nd turn's pre-check, before egress — exactly one call made.
    assert len(llm.calls) == 1

    escalations = [
        e for e in bus.published_of_type("audit.action_recorded")
        if e.payload.action_type == "governance.budget_exceeded"
    ]
    assert len(escalations) == 1
    assert escalations[0].payload.result == "escalated"
    deliverables.create_deliverable.assert_not_called()


# ── Backward compatibility: empty invocable_tools -> old single-shot path ──

async def test_empty_invocable_tools_uses_single_shot_path_unchanged() -> None:
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=LLMGenerateResponse(
        text=json.dumps({"hooks": ["Hook A", "Hook B", "Hook C"]}),
        provider="demo", concrete_model="demo-v1",
        usage=LLMUsage(prompt_tokens=50, completion_tokens=100, total_tokens=150),
    ))
    llm.generate_with_tools = AsyncMock(side_effect=AssertionError("must not be called"))
    deliverables, row = _deliverable_mock()

    # hook_generator_agent (real MVP contract) has invocable_tools=[] by
    # default — constructed with NO tools/authority/audit wired at all, to
    # prove the None defaults are genuinely safe for the untouched path.
    service = AgentExecutionService(registry=MVP_REGISTRY, llm=llm, deliverables=deliverables)
    result = await service.execute(
        org_id=ORG, agent_id="hook_generator_agent", input_data=_INPUT, user_id="u1",
    )
    assert result is row
    llm.generate.assert_called_once()
    llm.generate_with_tools.assert_not_called()


async def test_invocable_tools_without_wired_dependencies_fails_clearly() -> None:
    llm = MagicMock()
    deliverables, _ = _deliverable_mock()
    # Contract wants the tool loop, but no ToolProxy/GovernanceAuthority
    # injected — must fail with a clear config error, not an AttributeError.
    service = AgentExecutionService(
        registry=AgentRegistry([_TOOL_LOOP_CONTRACT]), llm=llm, deliverables=deliverables,
    )
    with pytest.raises(RuntimeError, match="invocable_tools"):
        await service.execute(
            org_id=ORG, agent_id="test_tool_loop_agent", input_data=_INPUT, user_id="u1",
        )


# ── Demo mode simulates a real tool call ────────────────────────────────────

async def test_demo_adapter_simulates_one_tool_call_then_finalizes() -> None:
    adapter = DemoLLMAdapter()
    tool_registry = ToolRegistry(build_builtin_tools(NullMemoryRecallPort()))
    tool = tool_registry.resolve("memory.search")

    req1 = LLMGenerateWithToolsRequest(
        system="You are a Hook Generator — produces ad/scroll-stopping hooks.",
        messages=[LLMMessage(role="user", content=[LLMContentBlock(kind="text", text="Generate hooks")])],
        requested_max_tokens=512, governance_token_id=uuid4(), org_id=ORG,
    )
    resp1 = await adapter.generate_with_tools(req1, [tool])
    assert resp1.stop_reason == "tool_use"
    assert len(resp1.content) == 1
    call = resp1.content[0]
    assert call.kind == "tool_use"
    assert call.tool_name == "memory.search"
    assert call.tool_input and "query" in call.tool_input  # required field filled plausibly

    messages2 = [
        *req1.messages,
        LLMMessage(role="assistant", content=resp1.content),
        LLMMessage(role="user", content=[LLMContentBlock(
            kind="tool_result", tool_use_id=call.tool_use_id, tool_output=json.dumps({"hits": []}),
        )]),
    ]
    req2 = LLMGenerateWithToolsRequest(
        system=req1.system, messages=messages2, requested_max_tokens=512,
        governance_token_id=req1.governance_token_id, org_id=ORG,
    )
    resp2 = await adapter.generate_with_tools(req2, [tool])
    assert resp2.stop_reason == "end_turn"
    payload = json.loads(resp2.text)
    assert "hooks" in payload
    assert any("[DEMO]" in hook for hook in payload["hooks"])


async def test_demo_mode_full_pipeline_produces_deliverable_via_tool_loop() -> None:
    """End-to-end: DemoLLMAdapter + real ToolProxy + real minted GovernanceToken."""
    authority, audit, bus, proxy = _harness()
    llm = DemoLLMAdapter()
    deliverables, row = _deliverable_mock()

    service = AgentExecutionService(
        registry=AgentRegistry([_TOOL_LOOP_CONTRACT]), llm=llm, deliverables=deliverables,
        tools=proxy, authority=authority, audit=audit,
    )
    result = await service.execute(
        org_id=ORG, agent_id="test_tool_loop_agent", input_data=_INPUT, user_id="u1",
    )
    assert result is row
    tool_events = [
        e for e in bus.published_of_type("audit.action_recorded")
        if e.payload.action_type == "tool.invoked"
    ]
    assert len(tool_events) == 1
    assert tool_events[0].payload.result == "success"
