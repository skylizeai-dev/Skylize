"""Decision Engine wired at the composition root (memory backend, no infra).

Covers the bootstrap contract: the container exposes a started engine; orgs in
`decision_engine_org_ids` are subscribed at startup; a proposal published on
the bus flows consume → evaluate → decision.* + audit; `aclose()` stops it.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from skylize.bootstrap import build_container
from skylize.config import Settings
from skylize.schemas.events.creative import CreativeReviewRequested

ORG = "org_test"


def _review_event(partition: str) -> CreativeReviewRequested:
    return CreativeReviewRequested(
        tenant_id=ORG,
        partition_key=partition,
        department="creative",
        source_agent_id="copy_director",
        correlation_id=uuid4(),
        payload=CreativeReviewRequested.Payload(
            brief_id=uuid4(),
            asset_ids=[uuid4()],
            proposed_action="approve_internal",
            proposed_spend_minor_units=None,
        ),
    )


async def _wait_for(bus, type_: str, n: int = 1) -> None:
    for _ in range(400):
        if len(bus.published_of_type(type_)) >= n:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"timed out waiting for {n}x {type_}")


async def test_engine_idle_by_default() -> None:
    c = await build_container(Settings(backend="memory"))
    try:
        assert c.decision_engine is not None
        # No orgs configured → wired but idle: no consumer tasks were spawned.
        assert not c.decision_engine._tasks
    finally:
        await c.aclose()


async def test_configured_org_flows_to_terminal_decision_and_audit() -> None:
    c = await build_container(
        Settings(backend="memory", decision_engine_org_ids=[ORG])
    )
    try:
        await c.bus.publish(_review_event("brief:wiring"))
        await _wait_for(c.bus, "decision.approved")
        assert c.bus.published_of_type("decision.evaluated")
        audits = c.bus.published_of_type("audit.action_recorded")
        assert any(
            a.payload.action_type == "decision.approved" for a in audits
        )
    finally:
        await c.aclose()


async def test_aclose_stops_consumers() -> None:
    c = await build_container(
        Settings(backend="memory", decision_engine_org_ids=[ORG])
    )
    assert c.decision_engine._tasks  # subscribed at startup
    await c.aclose()
    assert not c.decision_engine._tasks
