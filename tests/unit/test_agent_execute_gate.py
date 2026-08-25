"""AgentExecutionService synchronous decision gate (owner decisions D1/D3/D4/D5).

Memory-backend fakes only: the gate reuses the pure DecisionEvaluator, so the
full build-proposal -> evaluate -> emit -> map path runs with no infrastructure.
Governed orgs (governed_org_ids) are gated; every other org executes as today.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skylize.adapters.llm.gateway import LLMGenerateResponse, LLMUsage
from skylize.app.agents.execution import (
    AgentDeferredToHuman,
    AgentExecutionService,
)
from skylize.app.audit.service import AuditService
from skylize.app.decision_engine.evaluator import DecisionEvaluator
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.memory import (
    InMemoryAuditRepository,
    InMemoryCapitalRepository,
    InMemoryHitlQueueRepository,
)
from skylize.events.memory_bus import InMemoryEventBus

GOV_ORG = "org_governed"
UNGOV_ORG = "org_ungoverned"


def _llm(payload: dict[str, Any]) -> MagicMock:
    llm = MagicMock()
    llm.generate = AsyncMock(
        return_value=LLMGenerateResponse(
            text=json.dumps(payload),
            provider="demo",
            concrete_model="demo-v1",
            usage=LLMUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            cost_usd_micros=0,
        )
    )
    return llm


def _deliverables() -> MagicMock:
    row = MagicMock()
    row.id = uuid4()
    row.agent_id = "x"
    row.status = "draft"
    row.title = "t"
    svc = MagicMock()
    svc.create_deliverable = AsyncMock(return_value=row)
    return svc


def _service(
    *, governed: set[str], llm: MagicMock, deliverables: MagicMock
) -> tuple[AgentExecutionService, InMemoryEventBus, InMemoryHitlQueueRepository]:
    bus = InMemoryEventBus()
    hitl = InMemoryHitlQueueRepository()
    audit = AuditService(bus, InMemoryAuditRepository())
    service = AgentExecutionService(
        registry=MVP_REGISTRY,
        llm=llm,
        deliverables=deliverables,
        audit=audit,
        evaluator=DecisionEvaluator(registry=MVP_REGISTRY, capital=InMemoryCapitalRepository()),
        hitl=hitl,
        bus=bus,
        governed_org_ids=frozenset(governed),
    )
    return service, bus, hitl


def _audit_action_types(bus: InMemoryEventBus) -> list[str]:
    return [e.payload.action_type for e in bus.published_of_type("audit.action_recorded")]


# ── defer -> 202, hitl row written, SDK never invoked ────────────────────────

async def test_governed_defer_writes_hitl_and_skips_llm() -> None:
    llm = _llm({"hooks": ["a", "b", "c"]})
    deliverables = _deliverables()
    service, bus, hitl = _service(governed={GOV_ORG}, llm=llm, deliverables=deliverables)

    with pytest.raises(AgentDeferredToHuman) as ei:
        await service.execute(
            org_id=GOV_ORG,
            agent_id="hook_generator_agent",
            input_data={
                "brand_name": "Acme",
                "product_description": "A widget",
                "target_audience": "founders",
            },
            user_id="u1",
        )

    # No LLM call, no deliverable, no ledger row (the LLM is the only spend seam).
    llm.generate.assert_not_called()
    deliverables.create_deliverable.assert_not_called()

    # Exactly one hitl_queue row, and its id equals the one carried by the 202.
    rows = hitl.all()
    assert len(rows) == 1
    assert rows[0].hitl_id == ei.value.hitl_id
    assert rows[0].outcome == "deferred_to_human"
    assert rows[0].decision_id  # parent decision recorded alongside

    # Terminal event + audit emitted before the response (D5).
    assert bus.published_of_type("decision.evaluated")
    assert bus.published_of_type("decision.deferred_to_human")
    deferred = bus.published_of_type("decision.deferred_to_human")[0]
    assert deferred.payload.hitl_id == ei.value.hitl_id
    assert "decision.deferred_to_human" in _audit_action_types(bus)


# ── unmatched trigger -> defer (202), hitl row records the trigger ───────────

async def test_governed_unmatched_trigger_defers_and_records_trigger() -> None:
    llm = _llm({"anything": True})
    deliverables = _deliverables()
    service, bus, hitl = _service(governed={GOV_ORG}, llm=llm, deliverables=deliverables)

    # copy_director declares BRAND_LEGAL_SENSITIVE — a trigger the synchronous
    # vertical cannot specifically honour. Owner decision 2026-07-28: still
    # fail-closed (no LLM call without a human) but routed into the HITL queue
    # instead of dead-ending as a reject. Input must be valid (validation
    # precedes the gate).
    with pytest.raises(AgentDeferredToHuman) as ei:
        await service.execute(
            org_id=GOV_ORG,
            agent_id="copy_director",
            input_data={"brief_id": str(uuid4()), "product": "P", "audience": "A"},
            user_id="u1",
        )

    llm.generate.assert_not_called()
    deliverables.create_deliverable.assert_not_called()

    # The hitl_queue row records WHICH trigger caused the defer.
    rows = hitl.all()
    assert len(rows) == 1
    assert rows[0].hitl_id == ei.value.hitl_id
    assert rows[0].trigger_reason == "brand_legal_sensitive"
    assert bus.published_of_type("decision.evaluated")
    assert bus.published_of_type("decision.deferred_to_human")
    assert "decision.deferred_to_human" in _audit_action_types(bus)
    # No reject was emitted — the unmatched trigger no longer dead-ends.
    assert bus.published_of_type("decision.rejected") == []
    assert "decision.rejected" not in _audit_action_types(bus)


# ── approve -> execution proceeds (201 path), event + audit emitted ─────────

async def test_governed_approve_executes_and_emits() -> None:
    llm = _llm({"variants": ["v1", "v2"]})  # AdCopyOut (brief_id echoed from input)
    deliverables = _deliverables()
    service, bus, hitl = _service(governed={GOV_ORG}, llm=llm, deliverables=deliverables)

    row = await service.execute(
        org_id=GOV_ORG,
        agent_id="ad_copy_agent",  # no human_in_loop_triggers -> approves
        input_data={"brief_id": str(uuid4()), "hook": "H", "product": "P"},
        user_id="u1",
    )

    assert row is deliverables.create_deliverable.return_value
    llm.generate.assert_called_once()
    deliverables.create_deliverable.assert_called_once()
    assert hitl.all() == []
    # A decision that approves is still a decision and is recorded (D5).
    assert bus.published_of_type("decision.approved")
    assert "decision.approved" in _audit_action_types(bus)


# ── ungoverned org behaves exactly as before (D3) ───────────────────────────

async def test_ungoverned_org_executes_unchanged() -> None:
    llm = _llm({"hooks": ["a", "b", "c"]})
    deliverables = _deliverables()
    # hook_generator would DEFER if governed; on an ungoverned org the gate is
    # dormant and it executes exactly as today.
    service, bus, hitl = _service(governed={GOV_ORG}, llm=llm, deliverables=deliverables)

    row = await service.execute(
        org_id=UNGOV_ORG,
        agent_id="hook_generator_agent",
        input_data={
            "brand_name": "Acme",
            "product_description": "A widget",
            "target_audience": "founders",
        },
        user_id="u1",
    )

    assert row is deliverables.create_deliverable.return_value
    llm.generate.assert_called_once()
    deliverables.create_deliverable.assert_called_once()
    assert hitl.all() == []
    # No decision events at all — the gate never ran.
    assert bus.published_of_type("decision.evaluated") == []
    assert bus.published_of_type("decision.approved") == []
