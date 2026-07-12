"""Unit tests for DemoLLMAdapter."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from skylize.adapters.llm.demo_adapter import DemoLLMAdapter
from skylize.adapters.llm.gateway import LLMGenerateRequest, LLMGenerateResponse


def _make_request(prompt: str, system: str | None = None) -> LLMGenerateRequest:
    return LLMGenerateRequest(
        model="fast",
        prompt=prompt,
        system=system,
        requested_max_tokens=1024,
        governance_token_id=uuid4(),
        org_id="org_test",
    )


@pytest.mark.asyncio
async def test_demo_adapter_returns_valid_response() -> None:
    adapter = DemoLLMAdapter()
    req = _make_request("hook_generator_agent Generate hooks for my product.")
    resp = await adapter.generate(req)
    assert isinstance(resp, LLMGenerateResponse)
    assert resp.provider == "demo"
    assert resp.concrete_model == "demo-v1"
    assert resp.text  # non-empty


@pytest.mark.asyncio
async def test_demo_adapter_hook_output_is_valid_json_with_hooks() -> None:
    adapter = DemoLLMAdapter()
    req = _make_request("hook_generator_agent Generate 5 hooks.", system="You are hook_generator_agent.")
    resp = await adapter.generate(req)
    parsed = json.loads(resp.text)
    assert "hooks" in parsed
    assert isinstance(parsed["hooks"], list)
    assert len(parsed["hooks"]) >= 1


@pytest.mark.asyncio
async def test_demo_adapter_output_contains_demo_marker() -> None:
    adapter = DemoLLMAdapter()
    req = _make_request("hook_generator_agent Generate hooks.")
    resp = await adapter.generate(req)
    parsed = json.loads(resp.text)
    hooks: list[str] = parsed["hooks"]
    assert all("[DEMO]" in h for h in hooks)


@pytest.mark.asyncio
async def test_demo_adapter_usage_is_non_zero() -> None:
    adapter = DemoLLMAdapter()
    req = _make_request("hook_generator_agent test prompt")
    resp = await adapter.generate(req)
    assert resp.usage.total_tokens > 0
    assert resp.usage.prompt_tokens >= 0
    assert resp.usage.completion_tokens >= 0


@pytest.mark.asyncio
async def test_demo_adapter_cost_is_zero() -> None:
    adapter = DemoLLMAdapter()
    req = _make_request("hook_generator_agent Generate hooks.")
    resp = await adapter.generate(req)
    assert resp.cost_usd_micros == 0


@pytest.mark.asyncio
async def test_demo_adapter_simulates_latency(monkeypatch: pytest.MonkeyPatch) -> None:
    import skylize.adapters.llm.demo_adapter as mod
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
    adapter = DemoLLMAdapter()
    req = _make_request("hook_generator_agent Generate hooks.")
    await adapter.generate(req)
    assert len(slept) == 1
    assert 0.5 <= slept[0] <= 1.5


@pytest.mark.asyncio
async def test_demo_adapter_fallback_for_unknown_prompt() -> None:
    adapter = DemoLLMAdapter()
    req = _make_request("Do something completely unrelated and unknown.")
    resp = await adapter.generate(req)
    parsed = json.loads(resp.text)
    assert isinstance(parsed, dict)
