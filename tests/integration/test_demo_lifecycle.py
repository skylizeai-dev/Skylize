"""Full lifecycle on the demo adapter — the functional-without-keys milestone.

onboard → agent produces (demo LLM) → decision-bearing event → decision engine
→ HITL gate (deferred) → human verdict resumes → audit trail, all on the memory
backend with no API key and no infrastructure.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from skylize.bootstrap import build_container
from skylize.config import Settings
from skylize.schemas.events.creative import CreativeReviewRequested
from skylize.schemas.events.governance import GovernanceHumanApprovalReceived

ORG = "org_demo_e2e"


async def _wait_for(bus, type_: str, n: int = 1):
    for _ in range(600):
        events = bus.published_of_type(type_)
        if len(events) >= n:
            return events
        await asyncio.sleep(0.005)
    raise AssertionError(f"timed out waiting for {n}x {type_}")


async def test_full_lifecycle_on_demo_adapter() -> None:
    c = await build_container(
        Settings(backend="memory", decision_engine_org_ids=[ORG])
    )
    try:
        # 1. Onboard: provision the org and its first owner.
        tenant = await c.tenants.register(
            org_id=ORG,
            display_name="Demo E2E Org",
            owner_user_id="user_owner",
            correlation_id=uuid4(),
        )
        assert tenant.status == "active"

        # 2. Agent produces — the deterministic demo LLM, end to end through
        #    governance gate + content gate + output validation.
        result = await c.orchestrator.invoke(
            "hook_generator_agent",
            {
                "brand_name": "StrideCo",
                "product_description": "running shoes",
                "target_audience": "runners",
                "count": 3,
            },
            org_id=ORG,
        )
        assert result.status == "completed", result.reason
        assert result.output["hooks"]  # real (demo-marked) model output
        assert c.bus.published_of_type("creative.hooks_generated")

        # 3. A worker proposes an external launch of the produced asset — a
        #    decision-bearing event the engine consumes off the bus.
        await c.bus.publish(
            CreativeReviewRequested(
                tenant_id=ORG,
                partition_key="brief:e2e",
                department="creative",
                source_agent_id="hook_generator_agent",
                correlation_id=uuid4(),
                payload=CreativeReviewRequested.Payload(
                    brief_id=uuid4(),
                    asset_ids=[uuid4()],
                    proposed_action="launch",
                    proposed_spend_minor_units=None,
                ),
            )
        )

        # 4. HITL gate: a worker cannot launch externally — the engine defers
        #    to a human rather than approving or flat-rejecting.
        deferred = (await _wait_for(c.bus, "decision.deferred_to_human"))[0]
        assert c.bus.published_of_type("decision.evaluated")
        decision_id = deferred.payload.decision_id
        hitl_id = deferred.payload.hitl_id

        # 5. The human approves; the engine resumes the paused decision to its
        #    terminal outcome.
        await c.bus.publish(
            GovernanceHumanApprovalReceived(
                tenant_id=ORG,
                partition_key="brief:e2e",
                department="governance",
                correlation_id=uuid4(),
                payload=GovernanceHumanApprovalReceived.Payload(
                    decision_id=decision_id,
                    hitl_id=hitl_id,
                    approved=True,
                    decided_by="user_owner",
                ),
            )
        )
        approved = (await _wait_for(c.bus, "decision.approved"))[0]
        assert approved.payload.decision_id == decision_id

        # 6. The audit trail carries every stage of the lifecycle.
        audit_types = {
            a.payload.action_type
            for a in c.bus.published_of_type("audit.action_recorded")
        }
        assert {
            "tenant.registered",
            "orchestrator.run",
            "decision.deferred_to_human",
            "decision.approved",
        } <= audit_types
    finally:
        await c.aclose()
