"""Execution tests for `cfo_agent`'s budget_summary capability.

Covers the CRITICAL CONSTRAINT (CFO + Safety Suite are stateless): the
contract must not declare `memory.search` anywhere, and memory read/write
access must stay empty. Also covers the deterministic-computation rule —
`total`/`flags` are computed in Python from `line_items`, never trusted to
the (demo, templated) model output.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from skylize.adapters.llm.demo_adapter import DemoLLMAdapter
from skylize.app.agents.execution import AgentExecutionService, _compute_budget_summary
from skylize.app.audit.service import AuditService
from skylize.app.governance import GovernanceAuthority
from skylize.config import Settings
from skylize.contracts.registry import MVP_REGISTRY, AgentRegistry
from skylize.dal.memory import InMemoryAuditRepository, InMemoryGovernanceRepository
from skylize.events.memory_bus import InMemoryEventBus
from skylize.schemas.agents.finance import BudgetLineItem
from skylize.tools.builtin import build_builtin_tools
from skylize.tools.proxy import ToolProxy
from skylize.tools.registry import ToolRegistry

ORG = "org_test"

_INPUT = {
    "department": "marketing",
    "period": "2026-Q2",
    "line_items": [
        {"category": "paid_social", "amount": 45_000.0},
        {"category": "content", "amount": 20_000.0},
        {"category": "events", "amount": 10_000.0},
    ],
}


def _harness():
    contract = MVP_REGISTRY.resolve("cfo_agent")
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
    row.agent_id = "cfo_agent"
    svc.create_deliverable = AsyncMock(return_value=row)
    return svc, row


# ── Stateless constraint ────────────────────────────────────────────────────

def test_cfo_agent_is_stateless_no_memory_search() -> None:
    contract = MVP_REGISTRY.resolve("cfo_agent")
    assert "memory.search" not in contract.invocable_tools
    assert "memory.search" not in {g.tool_id for g in contract.allowed_tools}
    assert contract.memory_read_access == []
    assert contract.memory_write_access == []


def test_cfo_agent_only_invocable_tool_is_datetime() -> None:
    contract = MVP_REGISTRY.resolve("cfo_agent")
    assert contract.invocable_tools == ["utility.current_datetime"]


# ── Deterministic computation (pure function) ──────────────────────────────

def test_total_is_sum_of_line_item_amounts() -> None:
    items = [
        BudgetLineItem(category="paid_social", amount=45_000.0),
        BudgetLineItem(category="content", amount=20_000.0),
        BudgetLineItem(category="events", amount=10_000.0),
    ]
    total, _ = _compute_budget_summary(items)
    assert total == 75_000.0


def test_flag_triggered_when_single_line_item_exceeds_40_percent() -> None:
    items = [
        BudgetLineItem(category="paid_social", amount=45_000.0),
        BudgetLineItem(category="content", amount=20_000.0),
        BudgetLineItem(category="events", amount=10_000.0),
    ]
    total, flags = _compute_budget_summary(items)
    assert total == 75_000.0
    assert len(flags) == 1
    assert "paid_social" in flags[0]


def test_no_flags_when_spend_is_evenly_distributed() -> None:
    items = [
        BudgetLineItem(category="a", amount=10_000.0),
        BudgetLineItem(category="b", amount=10_000.0),
        BudgetLineItem(category="c", amount=10_000.0),
    ]
    _, flags = _compute_budget_summary(items)
    assert flags == []


def test_zero_total_produces_no_flags() -> None:
    items = [BudgetLineItem(category="a", amount=0.0)]
    total, flags = _compute_budget_summary(items)
    assert total == 0.0
    assert flags == []


# ── Full execution: demo adapter output total/flags overridden by Python ───

async def test_demo_execution_overrides_total_and_flags_with_python_computation() -> None:
    contract, authority, audit, bus, proxy = _harness()
    llm = DemoLLMAdapter()
    deliverables, row = _deliverable_mock()

    service = AgentExecutionService(
        registry=AgentRegistry([contract]), llm=llm, deliverables=deliverables,
        tools=proxy, authority=authority, audit=audit,
    )
    result = await service.execute(
        org_id=ORG, agent_id="cfo_agent", input_data=_INPUT, user_id="u1",
    )
    assert result is row

    content_markdown = deliverables.create_deliverable.call_args.kwargs["content_markdown"]
    # DemoLLMAdapter's template total is 0.0 — if this weren't overridden the
    # markdown would show $0.00 instead of the real sum of line_items.
    assert "$75,000.00" in content_markdown
    assert "paid_social" in content_markdown  # the 40%-concentration flag
    assert deliverables.create_deliverable.call_args.kwargs["deliverable_type"] == "other"
