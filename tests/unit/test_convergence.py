"""
Convergence kill (app/governance/authority.record_action).

Proves: an agent repeating the same action twice consecutively within a workflow
trips the circuit breaker with trip_reason="convergence", suspends the agent,
emits the breaker + suspension events exactly once, and records a
governance.convergence_failure audit action carrying the escalation_path. Also:
non-consecutive repeats do not trip, workflows/agents are isolated, and tripping
is idempotent (a third identical action neither re-suspends nor re-escalates).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from skylize.app.audit.service import AuditService
from skylize.app.governance import (
    CONVERGENCE_TRIP_REASON,
    ConvergenceTracker,
    GovernanceAuthority,
    GovernanceDenied,
    compute_action_hash,
)
from skylize.config import Settings
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.memory import InMemoryAuditRepository, InMemoryGovernanceRepository
from skylize.events.memory_bus import InMemoryEventBus

ORG = "org_test"
AGENT = "hook_generator_agent"  # registered in MVP_REGISTRY


def _authority():
    bus = InMemoryEventBus()
    audit = AuditService(bus, InMemoryAuditRepository())
    authority = GovernanceAuthority.build(
        repo=InMemoryGovernanceRepository(), audit=audit, bus=bus,
        registry=MVP_REGISTRY, settings=Settings(backend="memory"),
    )
    return authority, bus


# ---------------------------------------------------------------------------
# action_hash
# ---------------------------------------------------------------------------

def test_action_hash_deterministic_and_key_order_independent() -> None:
    a = compute_action_hash(agent_id=AGENT, action_type="llm.generate", action_args={"a": 1, "b": 2})
    b = compute_action_hash(agent_id=AGENT, action_type="llm.generate", action_args={"b": 2, "a": 1})
    assert a == b
    assert len(a) == 64


def test_action_hash_distinguishes_inputs() -> None:
    base = compute_action_hash(agent_id=AGENT, action_type="t", action_args={"x": 1})
    assert base != compute_action_hash(agent_id="other", action_type="t", action_args={"x": 1})
    assert base != compute_action_hash(agent_id=AGENT, action_type="u", action_args={"x": 1})
    assert base != compute_action_hash(agent_id=AGENT, action_type="t", action_args={"x": 2})


# ---------------------------------------------------------------------------
# ConvergenceTracker (pure ring buffer)
# ---------------------------------------------------------------------------

def test_tracker_trips_on_consecutive_repeat_only() -> None:
    tracker = ConvergenceTracker()
    corr = uuid4()
    assert tracker.record(corr, AGENT, "h1") is False  # first ever
    assert tracker.record(corr, AGENT, "h1") is True   # consecutive repeat → trip


def test_tracker_non_consecutive_repeat_does_not_trip() -> None:
    tracker = ConvergenceTracker()
    corr = uuid4()
    assert tracker.record(corr, AGENT, "h1") is False
    assert tracker.record(corr, AGENT, "h2") is False
    assert tracker.record(corr, AGENT, "h1") is False  # repeat, but not back-to-back


def test_tracker_isolates_workflow_and_agent() -> None:
    tracker = ConvergenceTracker()
    c1, c2 = uuid4(), uuid4()
    tracker.record(c1, AGENT, "h1")
    # Same hash, different workflow → not consecutive within that workflow.
    assert tracker.record(c2, AGENT, "h1") is False
    # Same hash + workflow, different agent → isolated.
    assert tracker.record(c1, "other_agent", "h1") is False


def test_tracker_reset_clears_history() -> None:
    tracker = ConvergenceTracker()
    corr = uuid4()
    tracker.record(corr, AGENT, "h1")
    tracker.reset(corr, AGENT)
    assert tracker.record(corr, AGENT, "h1") is False  # history gone → no trip


# ---------------------------------------------------------------------------
# Authority.record_action (integration with suspend + emit + escalate)
# ---------------------------------------------------------------------------

async def test_consecutive_action_trips_and_suspends() -> None:
    authority, bus = _authority()
    corr = uuid4()
    args = {"prompt": "loop"}

    first = await authority.record_action(
        agent_id=AGENT, org_id=ORG, correlation_id=corr,
        action_type="llm.generate", action_args=args,
    )
    assert first is False  # first action: recorded, no trip

    tripped = await authority.record_action(
        agent_id=AGENT, org_id=ORG, correlation_id=corr,
        action_type="llm.generate", action_args=args,
    )
    assert tripped is True

    # Agent is now suspended.
    with pytest.raises(GovernanceDenied):
        await authority.assert_active(AGENT, ORG)


async def test_trip_emits_convergence_reason_and_escalation_once() -> None:
    authority, bus = _authority()
    corr = uuid4()
    args = {"prompt": "loop"}
    for _ in range(2):
        await authority.record_action(
            agent_id=AGENT, org_id=ORG, correlation_id=corr,
            action_type="llm.generate", action_args=args,
        )

    breaker_events = bus.published_of_type("governance.circuit_breaker_tripped")
    suspended_events = bus.published_of_type("governance.agent_suspended")
    assert len(breaker_events) == 1  # exactly once
    assert len(suspended_events) == 1
    assert breaker_events[0].payload.trip_reason.startswith(CONVERGENCE_TRIP_REASON)

    # Convergence failure is audited with the escalation_path captured.
    audits = bus.published_of_type("audit.action_recorded")
    convergence_audits = [
        e for e in audits if e.payload.action_type == "governance.convergence_failure"
    ]
    assert len(convergence_audits) == 1
    assert convergence_audits[0].payload.result == "escalated"
    # escalation_path for hook_generator_agent walks the org tree to human_owner.
    assert "human_owner" in (convergence_audits[0].payload.result_reason or "")


async def test_third_identical_action_does_not_retrip_or_reescalate() -> None:
    authority, bus = _authority()
    corr = uuid4()
    args = {"prompt": "loop"}
    for _ in range(3):  # 1st records, 2nd trips, 3rd must be a no-op
        await authority.record_action(
            agent_id=AGENT, org_id=ORG, correlation_id=corr,
            action_type="llm.generate", action_args=args,
        )
    # Still exactly one of each governance signal — idempotent.
    assert len(bus.published_of_type("governance.circuit_breaker_tripped")) == 1
    assert len(bus.published_of_type("governance.agent_suspended")) == 1
    convergence_audits = [
        e for e in bus.published_of_type("audit.action_recorded")
        if e.payload.action_type == "governance.convergence_failure"
    ]
    assert len(convergence_audits) == 1


async def test_non_consecutive_actions_do_not_trip() -> None:
    authority, _ = _authority()
    corr = uuid4()
    await authority.record_action(
        agent_id=AGENT, org_id=ORG, correlation_id=corr,
        action_type="llm.generate", action_args={"prompt": "a"},
    )
    await authority.record_action(
        agent_id=AGENT, org_id=ORG, correlation_id=corr,
        action_type="llm.generate", action_args={"prompt": "b"},
    )
    tripped = await authority.record_action(
        agent_id=AGENT, org_id=ORG, correlation_id=corr,
        action_type="llm.generate", action_args={"prompt": "a"},  # repeat, not back-to-back
    )
    assert tripped is False
    await authority.assert_active(AGENT, ORG)  # still active


async def test_distinct_workflows_are_independent() -> None:
    authority, _ = _authority()
    args = {"prompt": "loop"}
    c1, c2 = uuid4(), uuid4()
    # Same action once in each of two workflows → neither is consecutive.
    assert await authority.record_action(
        agent_id=AGENT, org_id=ORG, correlation_id=c1,
        action_type="llm.generate", action_args=args,
    ) is False
    assert await authority.record_action(
        agent_id=AGENT, org_id=ORG, correlation_id=c2,
        action_type="llm.generate", action_args=args,
    ) is False
    await authority.assert_active(AGENT, ORG)
