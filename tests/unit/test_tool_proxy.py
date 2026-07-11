"""Unit tests for ToolProxy — the IF-TOOL enforcement gate.

Uses a real GovernanceAuthority (in-memory backend) so the governance-token
validation exercised here is the exact `contracts.token.validate_tool_call`
pipeline the tool proxy calls, not a stand-in.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from skylize.app.audit.service import AuditService
from skylize.app.governance import GovernanceAuthority
from skylize.config import Settings
from skylize.contracts.base import AgentContract, FailureMode, ToolGrant
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.memory import InMemoryAuditRepository, InMemoryGovernanceRepository
from skylize.events.memory_bus import InMemoryEventBus
from skylize.tools.base import (
    ToolCallLimitExceeded,
    ToolConvergenceDenied,
    ToolExecutionError,
    ToolInputError,
    ToolNotRegistered,
    ToolPermissionDenied,
)
from skylize.tools.builtin import build_builtin_tools
from skylize.tools.builtin.memory_recall import NullMemoryRecallPort
from skylize.tools.proxy import ToolProxy
from skylize.tools.registry import ToolRegistry

ORG = "org_test"


def _authority() -> tuple[GovernanceAuthority, InMemoryEventBus, AuditService]:
    bus = InMemoryEventBus()
    audit = AuditService(bus, InMemoryAuditRepository())
    authority = GovernanceAuthority.build(
        repo=InMemoryGovernanceRepository(), audit=audit, bus=bus,
        registry=MVP_REGISTRY, settings=Settings(backend="memory"),
    )
    return authority, bus, audit


def _proxy(authority: GovernanceAuthority, audit: AuditService) -> ToolProxy:
    registry = ToolRegistry(build_builtin_tools(NullMemoryRecallPort()))
    return ToolProxy(
        registry=registry, audit=audit,
        public_key=authority.public_key, live_state_for=authority.live_state_checker,
    )


def _proxy_with_convergence(authority: GovernanceAuthority, audit: AuditService) -> ToolProxy:
    registry = ToolRegistry(build_builtin_tools(NullMemoryRecallPort()))
    return ToolProxy(
        registry=registry, audit=audit,
        public_key=authority.public_key, live_state_for=authority.live_state_checker,
        record_action=authority.record_action,
    )


async def test_successful_invoke_returns_tool_result_and_audits() -> None:
    authority, bus, audit = _authority()
    proxy = _proxy(authority, audit)
    contract = MVP_REGISTRY.resolve("hook_generator_agent")  # allowed_tools includes memory.search
    corr = uuid4()
    token = await authority.mint(contract, org_id=ORG, correlation_id=corr)

    result = await proxy.invoke(
        tool_id="memory.search", input_data={"query": "high-performing hooks"},
        governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
    )
    assert result.tool_id == "memory.search"
    assert result.output_json() == {"hits": []}

    recorded = bus.published_of_type("audit.action_recorded")
    tool_events = [e for e in recorded if e.payload.action_type == "tool.invoked"]
    assert tool_events, "expected a tool.invoked audit event"
    assert tool_events[-1].payload.result == "success"


async def test_unknown_tool_id_raises_and_audits_failed() -> None:
    authority, bus, audit = _authority()
    proxy = _proxy(authority, audit)
    contract = MVP_REGISTRY.resolve("hook_generator_agent")
    corr = uuid4()
    token = await authority.mint(contract, org_id=ORG, correlation_id=corr)

    with pytest.raises(ToolNotRegistered):
        await proxy.invoke(
            tool_id="does.not.exist", input_data={}, governance_token=token,
            contract=contract, org_id=ORG, correlation_id=corr,
        )
    recorded = bus.published_of_type("audit.action_recorded")
    tool_events = [e for e in recorded if e.payload.action_type == "tool.invoked"]
    assert tool_events[-1].payload.result == "failed"


async def test_missing_scope_denied_and_audits_denied() -> None:
    authority, bus, audit = _authority()
    proxy = _proxy(authority, audit)
    contract = MVP_REGISTRY.resolve("hook_generator_agent")
    corr = uuid4()
    # Token scope defaults to hook_generator_agent's allowed_tools: llm.generate,
    # memory.search — utility.current_datetime is not in it.
    token = await authority.mint(contract, org_id=ORG, correlation_id=corr)

    with pytest.raises(ToolPermissionDenied) as excinfo:
        await proxy.invoke(
            tool_id="utility.current_datetime", input_data={}, governance_token=token,
            contract=contract, org_id=ORG, correlation_id=corr,
        )
    assert excinfo.value.failed_stage == "scope"

    recorded = bus.published_of_type("audit.action_recorded")
    tool_events = [e for e in recorded if e.payload.action_type == "tool.invoked"]
    assert tool_events[-1].payload.result == "denied"


async def test_revoked_token_denied() -> None:
    authority, _, audit = _authority()
    proxy = _proxy(authority, audit)
    contract = MVP_REGISTRY.resolve("hook_generator_agent")
    corr = uuid4()
    token = await authority.mint(contract, org_id=ORG, correlation_id=corr)
    await authority.revoke(token_id=token.token_id, agent_id=token.agent_id, org_id=ORG, reason="test", correlation_id=corr)

    with pytest.raises(ToolPermissionDenied) as excinfo:
        await proxy.invoke(
            tool_id="memory.search", input_data={"query": "x"}, governance_token=token,
            contract=contract, org_id=ORG, correlation_id=corr,
        )
    assert excinfo.value.failed_stage == "revocation"


async def test_input_validation_failure_raises_tool_input_error() -> None:
    authority, bus, audit = _authority()
    proxy = _proxy(authority, audit)
    contract = MVP_REGISTRY.resolve("hook_generator_agent")
    corr = uuid4()
    token = await authority.mint(contract, org_id=ORG, correlation_id=corr)

    with pytest.raises(ToolInputError):
        await proxy.invoke(
            tool_id="memory.search", input_data={"top_k": "not-an-int-and-no-query"},
            governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
        )
    recorded = bus.published_of_type("audit.action_recorded")
    tool_events = [e for e in recorded if e.payload.action_type == "tool.invoked"]
    assert tool_events[-1].payload.result == "failed"


async def test_handler_exception_wrapped_as_tool_execution_error() -> None:
    authority, _, audit = _authority()
    contract = MVP_REGISTRY.resolve("hook_generator_agent")
    corr = uuid4()
    token = await authority.mint(contract, org_id=ORG, correlation_id=corr)

    class _BoomPort:
        async def recall(self, *, query, top_k, org_id, namespace):
            raise RuntimeError("backend unreachable")

    from skylize.tools.builtin.memory_recall import build_memory_recall_tool

    registry = ToolRegistry([build_memory_recall_tool(_BoomPort())])
    proxy = ToolProxy(
        registry=registry, audit=audit,
        public_key=authority.public_key, live_state_for=authority.live_state_checker,
    )
    with pytest.raises(ToolExecutionError):
        await proxy.invoke(
            tool_id="memory.search", input_data={"query": "x"}, governance_token=token,
            contract=contract, org_id=ORG, correlation_id=corr,
        )


async def test_every_outcome_emits_exactly_one_audit_event() -> None:
    authority, bus, audit = _authority()
    proxy = _proxy(authority, audit)
    contract = MVP_REGISTRY.resolve("hook_generator_agent")
    corr = uuid4()
    token = await authority.mint(contract, org_id=ORG, correlation_id=corr)

    before = len([e for e in bus.published_of_type("audit.action_recorded") if e.payload.action_type == "tool.invoked"])
    await proxy.invoke(
        tool_id="memory.search", input_data={"query": "x"}, governance_token=token,
        contract=contract, org_id=ORG, correlation_id=corr,
    )
    after = len([e for e in bus.published_of_type("audit.action_recorded") if e.payload.action_type == "tool.invoked"])
    assert after == before + 1


# ── Convergence breaker (agent_governance.md §7) ────────────────────────────

async def test_repeated_identical_tool_call_trips_convergence_breaker() -> None:
    authority, bus, audit = _authority()
    proxy = _proxy_with_convergence(authority, audit)
    contract = MVP_REGISTRY.resolve("hook_generator_agent")
    corr = uuid4()
    token = await authority.mint(contract, org_id=ORG, correlation_id=corr)

    # First call: identical (tool_id, input) recorded, not yet a repeat.
    await proxy.invoke(
        tool_id="memory.search", input_data={"query": "brand voice"},
        governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
    )
    # Second call: same tool + same input, same workflow -> trips the breaker.
    with pytest.raises(ToolConvergenceDenied) as excinfo:
        await proxy.invoke(
            tool_id="memory.search", input_data={"query": "brand voice"},
            governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
        )
    assert excinfo.value.failed_stage == "convergence"

    # The Authority suspended the agent as a side effect of the trip.
    with pytest.raises(Exception):
        await authority.assert_active("hook_generator_agent", ORG)

    tool_events = [e for e in bus.published_of_type("audit.action_recorded") if e.payload.action_type == "tool.invoked"]
    assert tool_events[-1].payload.result == "denied"


async def test_different_input_does_not_trip_convergence_breaker() -> None:
    authority, bus, audit = _authority()
    proxy = _proxy_with_convergence(authority, audit)
    contract = MVP_REGISTRY.resolve("hook_generator_agent")
    corr = uuid4()
    token = await authority.mint(contract, org_id=ORG, correlation_id=corr)

    await proxy.invoke(
        tool_id="memory.search", input_data={"query": "brand voice"},
        governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
    )
    # Different input -> not a repeat -> no trip.
    result = await proxy.invoke(
        tool_id="memory.search", input_data={"query": "a totally different query"},
        governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
    )
    assert result.tool_id == "memory.search"


# ── Call-count ceiling (agent_governance.md §6) ─────────────────────────────

async def test_call_beyond_max_calls_per_run_is_denied() -> None:
    authority, bus, audit = _authority()
    proxy = _proxy(authority, audit)
    contract = AgentContract(
        agent_id="call_limit_test_agent",
        agent_role="Test Agent",
        authority_level="worker",
        department="test",
        input_schema="skylize.schemas.creative.HookRequestIn",
        output_schema="skylize.schemas.creative.HooksOut",
        allowed_tools=[
            ToolGrant(tool_id="memory.search", purpose="test", max_calls_per_run=2),
        ],
        max_token_budget=1_000,
        max_execution_time_seconds=30,
        escalation_path=["human_owner"],
        failure_mode=FailureMode.FAIL_CLOSED,
        memory_read_access=[],
        memory_write_access=[],
    )
    corr = uuid4()
    token = await authority.mint(contract, org_id=ORG, correlation_id=corr)

    # First two calls are within the grant's max_calls_per_run=2 and succeed.
    for i in range(2):
        result = await proxy.invoke(
            tool_id="memory.search", input_data={"query": f"q{i}"},
            governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
        )
        assert result.tool_id == "memory.search"

    # Third call to the same tool in the same workflow exceeds the ceiling.
    with pytest.raises(ToolCallLimitExceeded) as excinfo:
        await proxy.invoke(
            tool_id="memory.search", input_data={"query": "q2"},
            governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
        )
    assert excinfo.value.failed_stage == "call_limit"

    tool_events = [e for e in bus.published_of_type("audit.action_recorded") if e.payload.action_type == "tool.invoked"]
    assert tool_events[-1].payload.result == "denied"


async def test_no_record_action_wired_skips_convergence_tracking() -> None:
    authority, _, audit = _authority()
    proxy = _proxy(authority, audit)  # no record_action injected
    contract = MVP_REGISTRY.resolve("hook_generator_agent")
    corr = uuid4()
    token = await authority.mint(contract, org_id=ORG, correlation_id=corr)

    for _ in range(3):  # would trip a convergence breaker if tracking were active
        result = await proxy.invoke(
            tool_id="memory.search", input_data={"query": "brand voice"},
            governance_token=token, contract=contract, org_id=ORG, correlation_id=corr,
        )
        assert result.tool_id == "memory.search"
