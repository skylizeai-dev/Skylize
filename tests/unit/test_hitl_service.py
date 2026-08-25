"""HitlQueueService — verdicts on the synchronous gate's deferred requests.

Memory-backend fakes only: the same harness as test_agent_execute_gate.py plus
the HitlQueueService under test. Proves, without infrastructure:

  * approve replays the ORIGINAL request through execute() exactly once, with
    the gate satisfied (no second defer), and records verdict + event + audit;
  * the second verdict on a row is a typed refusal, never a re-execution;
  * reject records the verdict and executes nothing;
  * an expired / non-replayable / schema-drifted row is refused with the
    matching typed error, and a failed replay is disposed of by whether it can
    ever clear (owner decision D4): a PERMANENT failure (envelope invalid, input
    -schema drift, contract unregistered) moves the row to the terminal
    'expired' status, because request_json is frozen at enqueue so a release
    would loop it forever; a TRANSIENT failure (K12 — provider down, database
    unavailable, ceiling refusal) still releases the row to 'pending' so the
    approved work is never silently lost and a retry succeeds;
  * the ordinary execute path cannot satisfy the gate: without a
    HitlApprovalContext a governed defer-trigger agent still defers, and the
    HTTP request model forbids smuggling one in.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from skylize.adapters.llm.gateway import LLMGenerateResponse, LLMUsage
from skylize.app.agents.execution import (
    AgentDeferredToHuman,
    AgentExecutionService,
)
from skylize.app.audit.service import AuditService
from skylize.app.decision_engine.evaluator import DecisionEvaluator
from skylize.app.hitl.service import (
    HitlAlreadyActioned,
    HitlExecutionFailed,
    HitlExpired,
    HitlNotFound,
    HitlNotReplayable,
    HitlQueueService,
    HitlReplayInvalid,
)
from skylize.contracts.registry import MVP_REGISTRY
from skylize.dal.memory import (
    InMemoryAuditRepository,
    InMemoryCapitalRepository,
    InMemoryHitlQueueRepository,
)
from skylize.edge.routes.agents import ExecuteAgentRequest
from skylize.events.memory_bus import InMemoryEventBus

GOV_ORG = "org_governed"

_INPUT = {
    "brand_name": "Acme",
    "product_description": "A widget",
    "target_audience": "founders",
}


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
    row.agent_id = "hook_generator_agent"
    row.status = "draft"
    row.title = "t"
    svc = MagicMock()
    svc.create_deliverable = AsyncMock(return_value=row)
    return svc


def _harness(
    llm: MagicMock, deliverables: MagicMock
) -> tuple[HitlQueueService, AgentExecutionService, InMemoryHitlQueueRepository, InMemoryEventBus]:
    bus = InMemoryEventBus()
    hitl_repo = InMemoryHitlQueueRepository()
    audit = AuditService(bus, InMemoryAuditRepository())
    execution = AgentExecutionService(
        registry=MVP_REGISTRY,
        llm=llm,
        deliverables=deliverables,
        audit=audit,
        evaluator=DecisionEvaluator(registry=MVP_REGISTRY, capital=InMemoryCapitalRepository()),
        hitl=hitl_repo,
        bus=bus,
        governed_org_ids=frozenset({GOV_ORG}),
    )
    service = HitlQueueService(repo=hitl_repo, execution=execution, audit=audit, bus=bus)
    return service, execution, hitl_repo, bus


async def _defer(execution: AgentExecutionService):
    """Run the governed defer path; returns the raised AgentDeferredToHuman."""
    with pytest.raises(AgentDeferredToHuman) as ei:
        await execution.execute(
            org_id=GOV_ORG,
            agent_id="hook_generator_agent",
            input_data=dict(_INPUT),
            user_id="u1",
        )
    return ei.value


def _audit_action_types(bus: InMemoryEventBus) -> list[str]:
    return [e.payload.action_type for e in bus.published_of_type("audit.action_recorded")]


# ── approve: replay executes exactly once, gate satisfied, verdict recorded ──

async def test_approve_replays_once_and_records_verdict() -> None:
    llm = _llm({"hooks": ["a", "b", "c"]})
    deliverables = _deliverables()
    service, execution, repo, bus = _harness(llm, deliverables)
    deferred = await _defer(execution)
    llm.generate.assert_not_called()

    _, deliverable = await service.approve(
        org_id=GOV_ORG, hitl_id=deferred.hitl_id, reviewed_by="reviewer1", note="ship it"
    )

    # The SAME execute() path ran, exactly once, and did NOT defer again.
    llm.generate.assert_called_once()
    deliverables.create_deliverable.assert_called_once()
    assert deliverable is deliverables.create_deliverable.return_value

    item = await repo.get(deferred.hitl_id, GOV_ORG)
    assert item is not None
    assert item.status == "approved"
    assert item.verdict_by == "reviewer1"
    assert item.verdict_at is not None
    assert item.verdict_json is not None
    assert item.verdict_json["deliverable_id"] == str(deliverable.id)
    assert item.verdict_json["note"] == "ship it"

    # Terminal decision event + audit, emitted synchronously (item 9).
    approved_events = bus.published_of_type("decision.approved")
    assert len(approved_events) == 1
    assert approved_events[0].causation_id == item.correlation_id  # K8 chain
    assert "hitl.approved" in _audit_action_types(bus)

    # Deliverable metadata marks the replay provenance.
    metadata = deliverables.create_deliverable.call_args.kwargs["metadata"]
    assert metadata["replay_of_hitl_id"] == str(deferred.hitl_id)
    assert metadata["user_id"] == "u1"  # original requester, not the reviewer


async def test_second_approve_is_typed_refusal_not_reexecution() -> None:
    llm = _llm({"hooks": ["a", "b", "c"]})
    deliverables = _deliverables()
    service, execution, repo, bus = _harness(llm, deliverables)
    deferred = await _defer(execution)

    await service.approve(org_id=GOV_ORG, hitl_id=deferred.hitl_id, reviewed_by="r1")
    with pytest.raises(HitlAlreadyActioned) as ei:
        await service.approve(org_id=GOV_ORG, hitl_id=deferred.hitl_id, reviewed_by="r2")

    assert ei.value.status == "approved"
    llm.generate.assert_called_once()  # never a second execution
    deliverables.create_deliverable.assert_called_once()
    item = await repo.get(deferred.hitl_id, GOV_ORG)
    assert item is not None and item.verdict_by == "r1"  # verdict not corrupted


# ── reject: verdict recorded, nothing executes ──────────────────────────────

async def test_reject_records_verdict_and_executes_nothing() -> None:
    llm = _llm({"hooks": ["a"]})
    deliverables = _deliverables()
    service, execution, repo, bus = _harness(llm, deliverables)
    deferred = await _defer(execution)

    await service.reject(
        org_id=GOV_ORG, hitl_id=deferred.hitl_id, reviewed_by="r1", note="not now"
    )

    llm.generate.assert_not_called()
    deliverables.create_deliverable.assert_not_called()
    item = await repo.get(deferred.hitl_id, GOV_ORG)
    assert item is not None and item.status == "rejected"
    assert item.verdict_by == "r1"
    rejected_events = bus.published_of_type("decision.rejected")
    assert len(rejected_events) == 1
    assert rejected_events[0].payload.stage_rejected_at == "hitl_gate"
    assert "hitl.rejected" in _audit_action_types(bus)

    with pytest.raises(HitlAlreadyActioned):
        await service.approve(org_id=GOV_ORG, hitl_id=deferred.hitl_id, reviewed_by="r2")
    llm.generate.assert_not_called()


# ── typed refusals: not found, expired, not replayable ──────────────────────

async def test_unknown_hitl_id_not_found() -> None:
    service, _, _, _ = _harness(_llm({}), _deliverables())
    with pytest.raises(HitlNotFound):
        await service.approve(org_id=GOV_ORG, hitl_id=uuid4(), reviewed_by="r1")


async def test_expired_row_refused_and_left_pending() -> None:
    llm = _llm({"hooks": ["a"]})
    service, execution, repo, _ = _harness(llm, _deliverables())
    deferred = await _defer(execution)

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    repo._rows[0] = replace(repo._rows[0], expires_at=past)

    with pytest.raises(HitlExpired):
        await service.approve(org_id=GOV_ORG, hitl_id=deferred.hitl_id, reviewed_by="r1")
    llm.generate.assert_not_called()
    item = await repo.get(deferred.hitl_id, GOV_ORG)
    assert item is not None and item.status == "pending"


async def test_row_without_request_json_not_replayable() -> None:
    llm = _llm({"hooks": ["a"]})
    service, execution, repo, _ = _harness(llm, _deliverables())
    deferred = await _defer(execution)

    repo._rows[0] = replace(repo._rows[0], request_json=None)

    with pytest.raises(HitlNotReplayable):
        await service.approve(org_id=GOV_ORG, hitl_id=deferred.hitl_id, reviewed_by="r1")
    llm.generate.assert_not_called()


# ── K7: schema drift fails loudly and TERMINATES the row (D4) ───────────────
#
# This assertion inverted deliberately. request_json is written once at enqueue
# and never rewritten, so a release put the row back in the pending list where
# the identical failure recurred on every approval: pending -> approve -> fail
# -> pending, accumulating hitl.approve_failed audit rows and leaving a
# permanently unactionable item in the reviewer's queue. Owner decision D4: a
# PERMANENT failure moves the row to the terminal 'expired' status.

async def test_replay_invalid_input_terminates_row_as_expired() -> None:
    llm = _llm({"hooks": ["a"]})
    deliverables = _deliverables()
    service, execution, repo, bus = _harness(llm, deliverables)
    deferred = await _defer(execution)

    stored = repo._rows[0]
    assert stored.request_json is not None
    repo._rows[0] = replace(stored, request_json={**stored.request_json, "input": {}})

    with pytest.raises(HitlReplayInvalid):
        await service.approve(org_id=GOV_ORG, hitl_id=deferred.hitl_id, reviewed_by="r1")

    llm.generate.assert_not_called()
    deliverables.create_deliverable.assert_not_called()
    item = await repo.get(deferred.hitl_id, GOV_ORG)
    assert item is not None and item.status == "expired"  # terminal, not looping
    assert item.verdict_by == "r1"  # a human really did approve; history kept
    assert "hitl.approve_failed" in _audit_action_types(bus)

    # It is gone from the reviewer's queue and cannot be re-approved.
    pending, total = await service.list_pending(GOV_ORG)
    assert [i.hitl_id for i in pending] == []
    assert total == 0
    with pytest.raises(HitlAlreadyActioned):
        await service.approve(org_id=GOV_ORG, hitl_id=deferred.hitl_id, reviewed_by="r2")


async def test_invalid_stored_envelope_terminates_row_as_expired() -> None:
    """The other PERMANENT path: request_json is not a valid HitlReplayEnvelope."""
    llm = _llm({"hooks": ["a"]})
    deliverables = _deliverables()
    service, execution, repo, bus = _harness(llm, deliverables)
    deferred = await _defer(execution)

    stored = repo._rows[0]
    repo._rows[0] = replace(stored, request_json={"totally": "wrong shape"})

    with pytest.raises(HitlNotReplayable):
        await service.approve(org_id=GOV_ORG, hitl_id=deferred.hitl_id, reviewed_by="r1")

    llm.generate.assert_not_called()
    item = await repo.get(deferred.hitl_id, GOV_ORG)
    assert item is not None and item.status == "expired"
    assert "hitl.approve_failed" in _audit_action_types(bus)
    assert (await service.list_pending(GOV_ORG))[1] == 0


async def test_unregistered_contract_terminates_row_as_expired() -> None:
    """The third PERMANENT path: the stored agent_id is no longer registered."""
    llm = _llm({"hooks": ["a"]})
    deliverables = _deliverables()
    service, execution, repo, bus = _harness(llm, deliverables)
    deferred = await _defer(execution)

    stored = repo._rows[0]
    assert stored.request_json is not None
    repo._rows[0] = replace(
        stored, request_json={**stored.request_json, "agent_id": "agent_that_left"}
    )

    with pytest.raises(HitlReplayInvalid):
        await service.approve(org_id=GOV_ORG, hitl_id=deferred.hitl_id, reviewed_by="r1")

    llm.generate.assert_not_called()
    item = await repo.get(deferred.hitl_id, GOV_ORG)
    assert item is not None and item.status == "expired"
    assert (await service.list_pending(GOV_ORG))[1] == 0


# ── K12: TRANSIENT execution failure after the claim releases the row ───────

async def test_execution_failure_releases_row_to_pending() -> None:
    llm = _llm({"hooks": ["a"]})
    llm.generate = AsyncMock(side_effect=RuntimeError("provider down"))
    deliverables = _deliverables()
    service, execution, repo, bus = _harness(llm, deliverables)
    deferred = await _defer(execution)

    with pytest.raises(HitlExecutionFailed):
        await service.approve(org_id=GOV_ORG, hitl_id=deferred.hitl_id, reviewed_by="r1")

    deliverables.create_deliverable.assert_not_called()
    item = await repo.get(deferred.hitl_id, GOV_ORG)
    assert item is not None and item.status == "pending"  # work not lost
    assert "hitl.approve_failed" in _audit_action_types(bus)

    # The row is actionable again: a retry after the outage succeeds.
    llm.generate = AsyncMock(
        return_value=LLMGenerateResponse(
            text=json.dumps({"hooks": ["a", "b", "c"]}),
            provider="demo",
            concrete_model="demo-v1",
            usage=LLMUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            cost_usd_micros=0,
        )
    )
    _, deliverable = await service.approve(
        org_id=GOV_ORG, hitl_id=deferred.hitl_id, reviewed_by="r1"
    )
    assert deliverable is deliverables.create_deliverable.return_value


# ── the ordinary path cannot satisfy the gate ───────────────────────────────

async def test_ordinary_execute_path_still_defers_without_approval_context() -> None:
    # The bypass is an optional keyword-only object parameter with default
    # None. The ordinary path (route -> execute) never passes it, so a governed
    # defer-trigger agent defers EVERY time — a pending hitl row for the same
    # agent does not satisfy later gates.
    llm = _llm({"hooks": ["a"]})
    service, execution, repo, _ = _harness(llm, _deliverables())
    first = await _defer(execution)
    second = await _defer(execution)
    assert first.hitl_id != second.hitl_id  # two independent deferrals
    llm.generate.assert_not_called()


def test_http_request_model_cannot_carry_gate_bypass() -> None:
    # ExecuteAgentRequest is extra="forbid": a body that tries to smuggle a
    # hitl_approval (or any bypass flag) fails validation at the edge, before
    # the service is ever called.
    for extra_field in ("hitl_approval", "gate_bypass", "approved"):
        with pytest.raises(ValidationError):
            ExecuteAgentRequest.model_validate(
                {"agent_id": "hook_generator_agent", "input": {}, extra_field: True}
            )
