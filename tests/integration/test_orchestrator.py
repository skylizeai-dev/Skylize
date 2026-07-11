"""Orchestrator end-to-end on the memory backend (no infra, no LLM)."""

from __future__ import annotations

from uuid import uuid4

from skylize.bootstrap import build_container
from skylize.config import Settings

ORG = "org_test"


async def _container():
    return await build_container(Settings(backend="memory"))


async def test_creative_run_completes_and_emits_event() -> None:
    c = await _container()
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
    assert result.token_id is not None
    assert result.event_type == "creative.hooks_generated"
    # The agent step is a real model call (demo adapter here) — hook count is
    # model-determined; assert real output rather than a stub's shape.
    assert result.output["hooks"]

    # The business event and a success audit were published.
    assert c.bus.published_of_type("creative.hooks_generated")
    audits = c.bus.published_of_type("audit.action_recorded")
    assert any(True for _ in audits)
    await c.aclose()


async def test_unknown_agent_is_denied_fail_closed() -> None:
    c = await _container()
    result = await c.orchestrator.invoke("does_not_exist", {}, org_id=ORG)
    assert result.status == "denied"
    assert "not registered" in (result.reason or "")
    await c.aclose()


async def test_invalid_input_fails_before_minting() -> None:
    c = await _container()
    # Missing required 'product'/'audience' for HookRequestIn.
    result = await c.orchestrator.invoke(
        "hook_generator_agent", {"brief_id": uuid4()}, org_id=ORG
    )
    assert result.status == "failed"
    assert "invalid input" in (result.reason or "")
    await c.aclose()


async def test_kill_switch_blocks_run() -> None:
    c = await _container()
    await c.authority.engage_kill_switch(
        scope_type="agent", scope_id="hook_generator_agent", org_id=ORG,
        engaged_by="user_owner", reason="incident", correlation_id=uuid4(),
    )
    result = await c.orchestrator.invoke(
        "hook_generator_agent",
        {"brief_id": uuid4(), "product": "x", "audience": "y"},
        org_id=ORG,
    )
    assert result.status == "denied"
    assert "kill switch" in (result.reason or "")
    await c.aclose()
