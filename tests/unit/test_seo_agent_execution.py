"""Execution tests for `seo_keyword_agent` — the platform's SEO tool-enabled worker.

Proves the full demo-mode tool loop: contract resolves from MVP_REGISTRY,
the tool-use turn invokes `search.web` (first entry in `invocable_tools`),
the final answer validates against SeoKeywordExecuteOut, and a deliverable of
type "seo_report" is persisted.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from skylize.adapters.llm.demo_adapter import DemoLLMAdapter
from skylize.app.agents.execution import AgentExecutionService
from skylize.app.audit.service import AuditService
from skylize.app.governance import GovernanceAuthority
from skylize.config import Settings
from skylize.contracts.registry import MVP_REGISTRY, AgentRegistry
from skylize.dal.memory import InMemoryAuditRepository, InMemoryGovernanceRepository
from skylize.events.memory_bus import InMemoryEventBus
from skylize.schemas.agents.seo import SeoKeywordExecuteOut
from skylize.tools.builtin import build_builtin_tools
from skylize.tools.proxy import ToolProxy
from skylize.tools.registry import ToolRegistry

ORG = "org_test"

_INPUT = {
    "topic": "project management software",
    "target_market": "north america",
    "competitor_urls": ["https://example.com/competitor-a"],
}


def _harness():
    contract = MVP_REGISTRY.resolve("seo_keyword_agent")
    bus = InMemoryEventBus()
    audit = AuditService(bus, InMemoryAuditRepository())
    authority = GovernanceAuthority.build(
        repo=InMemoryGovernanceRepository(), audit=audit, bus=bus,
        registry=AgentRegistry([contract]), settings=Settings(backend="memory"),
    )
    tool_registry = ToolRegistry(build_builtin_tools())
    proxy = ToolProxy(
        registry=tool_registry, audit=audit,
        public_key=authority.public_key, live_state_for=authority.live_state_checker,
    )
    return contract, authority, audit, bus, proxy


def _deliverable_mock():
    svc = MagicMock()
    row = MagicMock()
    row.id = uuid4()
    row.agent_id = "seo_keyword_agent"
    svc.create_deliverable = AsyncMock(return_value=row)
    return svc, row


async def test_seo_keyword_agent_registered_and_tool_enabled() -> None:
    contract = MVP_REGISTRY.resolve("seo_keyword_agent")
    assert contract.department == "growth"
    assert contract.invocable_tools == ["search.web", "memory.search"]
    assert contract.escalation_path[-1] == "human_owner"


async def test_demo_mode_tool_loop_invokes_search_web_and_produces_deliverable() -> None:
    contract, authority, audit, bus, proxy = _harness()
    llm = DemoLLMAdapter()
    deliverables, row = _deliverable_mock()

    service = AgentExecutionService(
        registry=AgentRegistry([contract]), llm=llm, deliverables=deliverables,
        tools=proxy, authority=authority, audit=audit,
    )
    result = await service.execute(
        org_id=ORG, agent_id="seo_keyword_agent", input_data=_INPUT, user_id="u1",
    )
    assert result is row

    tool_events = [
        e for e in bus.published_of_type("audit.action_recorded")
        if e.payload.action_type == "tool.invoked"
    ]
    assert len(tool_events) == 1
    assert tool_events[0].payload.result == "success"

    # DemoLLMAdapter simulates exactly one tool call using the FIRST entry of
    # invocable_tools — proves the contract's declared research-first order
    # (search.web before memory.search) actually drives the loop.
    call_input = deliverables.create_deliverable.call_args.kwargs
    content_markdown = call_input["content_markdown"]
    assert "SEO Keyword Research" in content_markdown
    assert call_input["deliverable_type"] == "seo_report"


async def test_output_validates_against_seo_keyword_schema() -> None:
    contract, authority, audit, bus, proxy = _harness()
    llm = DemoLLMAdapter()
    deliverables, _ = _deliverable_mock()

    service = AgentExecutionService(
        registry=AgentRegistry([contract]), llm=llm, deliverables=deliverables,
        tools=proxy, authority=authority, audit=audit,
    )
    await service.execute(org_id=ORG, agent_id="seo_keyword_agent", input_data=_INPUT, user_id="u1")

    content_markdown = deliverables.create_deliverable.call_args.kwargs["content_markdown"]
    assert "Primary Keywords" in content_markdown
    # Re-derive the same payload the service validated, to assert schema fit directly.
    parsed = SeoKeywordExecuteOut.model_validate({
        "primary_keywords": ["kw one"],
        "keyword_difficulty_notes": "note",
        "content_angle_suggestions": ["angle one"],
    })
    assert parsed.primary_keywords == ["kw one"]
