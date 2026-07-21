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


async def test_flag_off_wires_the_inline_engine_with_reclaim_enabled() -> None:
    """FLAG OFF (SKYLIZE_DECISION_ENGINE unset ⇒ 'inline') — asserted, not assumed.

    PEL reclaim lives on the SHARED adapter, so it changes the inline engine too
    even though the flag is untouched. Two things must hold and both are checked
    here rather than inferred: the default flag still wires the inline engine (no
    accidental flip), and the bus it gets has a non-zero reclaim window — a zero
    window would let a sibling steal messages from a healthy worker mid-flight.
    """
    from skylize.events.redis_adapter import (
        DEFAULT_RECLAIM_MIN_IDLE_MS,
        RedisEventBus,
    )

    settings = Settings(backend="memory")
    assert settings.decision_engine == "inline", "the flag default moved"

    c = await build_container(settings)
    try:
        assert c.decision_engine is not None  # inline engine, as before
    finally:
        await c.aclose()

    # The postgres backend hands the inline engine this adapter with no reclaim
    # override, so the default IS the inline engine's effective window.
    assert DEFAULT_RECLAIM_MIN_IDLE_MS == 60_000
    bus = RedisEventBus("redis://localhost:6379")
    assert bus._reclaim_min_idle_ms == DEFAULT_RECLAIM_MIN_IDLE_MS
    assert bus._reclaim_batch > 0, "a zero batch would silently disable reclaim"
    await bus.close()


async def test_opa_worker_wires_the_idle_knob_that_was_dead_config() -> None:
    """`redis_idle_time_ms` is now load-bearing, not unused config.

    It named the reclaim window all along but nothing read it. Guard that the
    worker's bus actually carries it, so the knob cannot rot back into a no-op.
    """
    import inspect

    from skylize.decision_engine import worker

    assert "reclaim_min_idle_ms=de_settings.redis_idle_time_ms" in inspect.getsource(worker)


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
