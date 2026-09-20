"""AgentExecutionService writes to the console's notification feed at its two
real producer points, and a notification failure never breaks the action.

A fake evaluator drives both terminal outcomes deterministically (`deferred_to_
human` and `rejected`), independent of MVP_REGISTRY's actual trigger rules,
which test_agent_execute_gate.py already covers for the governance mechanics
themselves. This file is about the ADDITIONAL side effect only: does the right
notification kind land, and does the gate survive when it cannot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from skylize.adapters.llm.gateway import LLMGenerateResponse, LLMUsage
from skylize.app.agents.execution import (
    AgentDeferredToHuman,
    AgentExecutionService,
    AgentGovernanceRejected,
)
from skylize.app.audit.service import AuditService
from skylize.app.decision_engine.events import DecisionResult
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.memory import InMemoryAuditRepository, InMemoryHitlQueueRepository
from skylize.events.memory_bus import InMemoryEventBus

GOV_ORG = "org_governed"
AGENT_ID = "hook_generator_agent"


@dataclass
class _FakeNotificationsDAL:
    """Records every call; `fail` makes `record` behave like a real DB outage
    (swallowed, never raised) so callers can assert best-effort behaviour."""

    fail: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def record(self, **kwargs: Any) -> UUID | None:
        if self.fail:
            return None
        self.calls.append(kwargs)
        return uuid4()


class _FixedOutcomeEvaluator:
    """Returns the same DecisionResult for every proposal it is given."""

    def __init__(self, outcome: str, *, hitl_trigger: str | None = None) -> None:
        self._outcome = outcome
        self._hitl_trigger = hitl_trigger

    async def evaluate(self, proposal: Any) -> DecisionResult:
        return DecisionResult(
            proposal_id=proposal.proposal_id,
            decision_id=proposal.proposal_id,
            proposing_agent=proposal.proposing_agent_id,
            action_kind=proposal.action_kind,
            outcome=self._outcome,
            stages_completed=["fixed"],
            reasons=["fixed outcome for test"],
            hitl_trigger=self._hitl_trigger,
            policy_version="test",
        )


def _llm() -> MagicMock:
    llm = MagicMock()
    llm.generate = AsyncMock(
        return_value=LLMGenerateResponse(
            text=json.dumps({"hooks": ["a"]}),
            provider="demo",
            concrete_model="demo-v1",
            usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            cost_usd_micros=0,
        )
    )
    return llm


def _deliverables() -> MagicMock:
    row = MagicMock()
    row.id = uuid4()
    svc = MagicMock()
    svc.create_deliverable = AsyncMock(return_value=row)
    return svc


def _service(
    *, outcome: str, notifications: _FakeNotificationsDAL
) -> AgentExecutionService:
    bus = InMemoryEventBus()
    hitl = InMemoryHitlQueueRepository()
    audit = AuditService(bus, InMemoryAuditRepository())
    return AgentExecutionService(
        registry=MVP_REGISTRY,
        llm=_llm(),
        deliverables=_deliverables(),
        audit=audit,
        evaluator=_FixedOutcomeEvaluator(outcome, hitl_trigger="TEST_TRIGGER"),
        hitl=hitl,
        bus=bus,
        governed_org_ids=frozenset({GOV_ORG}),
        notifications=notifications,
    )


async def _run(service: AgentExecutionService) -> None:
    await service.execute(
        org_id=GOV_ORG,
        agent_id=AGENT_ID,
        input_data={
            "brand_name": "Acme",
            "product_description": "A widget",
            "target_audience": "founders",
        },
        user_id="u1",
    )


# ---------------------------------------------------------------------------
# hitl.approval_requested
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deferral_writes_a_hitl_approval_requested_notification() -> None:
    notifications = _FakeNotificationsDAL()
    service = _service(outcome="deferred_to_human", notifications=notifications)

    with pytest.raises(AgentDeferredToHuman):
        await _run(service)

    assert len(notifications.calls) == 1
    call = notifications.calls[0]
    assert call["org_id"] == GOV_ORG
    assert call["kind"] == "hitl.approval_requested"
    assert call["severity"] == "warning"
    assert call["correlation_id"] is not None


@pytest.mark.asyncio
async def test_a_notifications_outage_does_not_break_the_deferral() -> None:
    """BEST EFFORT: the 202 (raised as AgentDeferredToHuman) must still happen
    even when the notification write fails, exactly as a Slack outage must
    never fail the request that produced it."""
    notifications = _FakeNotificationsDAL(fail=True)
    service = _service(outcome="deferred_to_human", notifications=notifications)

    with pytest.raises(AgentDeferredToHuman):
        await _run(service)
    # No exception propagated from the notification write; the fake's own
    # `fail` branch stands in for NotificationsDAL.record's swallow-and-log.


@pytest.mark.asyncio
async def test_deferral_is_unaffected_when_no_notifications_dal_is_wired() -> None:
    """The memory backend: notifications=None must not change gate behaviour."""
    bus = InMemoryEventBus()
    hitl = InMemoryHitlQueueRepository()
    audit = AuditService(bus, InMemoryAuditRepository())
    service = AgentExecutionService(
        registry=MVP_REGISTRY,
        llm=_llm(),
        deliverables=_deliverables(),
        audit=audit,
        evaluator=_FixedOutcomeEvaluator("deferred_to_human", hitl_trigger="T"),
        hitl=hitl,
        bus=bus,
        governed_org_ids=frozenset({GOV_ORG}),
        notifications=None,
    )
    with pytest.raises(AgentDeferredToHuman):
        await _run(service)


# ---------------------------------------------------------------------------
# governance.action_denied
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejection_writes_a_governance_action_denied_notification() -> None:
    notifications = _FakeNotificationsDAL()
    service = _service(outcome="rejected", notifications=notifications)

    with pytest.raises(AgentGovernanceRejected):
        await _run(service)

    assert len(notifications.calls) == 1
    call = notifications.calls[0]
    assert call["org_id"] == GOV_ORG
    assert call["kind"] == "governance.action_denied"
    assert call["severity"] == "critical"


@pytest.mark.asyncio
async def test_a_notifications_outage_does_not_break_the_rejection() -> None:
    notifications = _FakeNotificationsDAL(fail=True)
    service = _service(outcome="rejected", notifications=notifications)

    with pytest.raises(AgentGovernanceRejected):
        await _run(service)
