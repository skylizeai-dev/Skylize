"""Unit tests for DemoLLMAdapter."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from skylize.adapters.llm.demo_adapter import (
    _DEMO_RESPONSES,
    DemoLLMAdapter,
    DemoResponseUnavailable,
)
from skylize.adapters.llm.gateway import LLMGenerateRequest, LLMGenerateResponse
from skylize.contracts.registry import MVP_REGISTRY

# An agent_id no contract uses -- the "not demoable" case.
UNKNOWN_AGENT = "agent_with_no_demo_payload"


def _make_request(
    prompt: str,
    system: str | None = None,
    *,
    agent_id: str = "hook_generator_agent",
) -> LLMGenerateRequest:
    return LLMGenerateRequest(
        model="fast",
        prompt=prompt,
        system=system,
        requested_max_tokens=1024,
        governance_token_id=uuid4(),
        org_id="org_test",
        correlation_id=uuid4(),
        agent_id=agent_id,
    )


@pytest.mark.asyncio
async def test_demo_adapter_returns_valid_response() -> None:
    adapter = DemoLLMAdapter()
    req = _make_request("Generate hooks for my product.")
    resp = await adapter.generate(req)
    assert isinstance(resp, LLMGenerateResponse)
    assert resp.provider == "demo"
    assert resp.concrete_model == "demo-v1"
    assert resp.text  # non-empty


@pytest.mark.asyncio
async def test_demo_adapter_hook_output_is_valid_json_with_hooks() -> None:
    adapter = DemoLLMAdapter()
    req = _make_request("Generate 5 hooks.", system="You are a Hook Generator.")
    resp = await adapter.generate(req)
    parsed = json.loads(resp.text)
    assert "hooks" in parsed
    assert isinstance(parsed["hooks"], list)
    assert len(parsed["hooks"]) >= 1


@pytest.mark.asyncio
async def test_demo_adapter_output_contains_demo_marker() -> None:
    adapter = DemoLLMAdapter()
    req = _make_request("Generate hooks.")
    resp = await adapter.generate(req)
    parsed = json.loads(resp.text)
    hooks: list[str] = parsed["hooks"]
    assert all("[DEMO]" in h for h in hooks)


@pytest.mark.asyncio
async def test_demo_adapter_usage_is_non_zero() -> None:
    adapter = DemoLLMAdapter()
    req = _make_request("test prompt")
    resp = await adapter.generate(req)
    assert resp.usage.total_tokens > 0
    assert resp.usage.prompt_tokens >= 0
    assert resp.usage.completion_tokens >= 0


@pytest.mark.asyncio
async def test_demo_adapter_cost_is_zero() -> None:
    adapter = DemoLLMAdapter()
    req = _make_request("Generate hooks.")
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
    req = _make_request("Generate hooks.")
    await adapter.generate(req)
    assert len(slept) == 1
    assert 0.5 <= slept[0] <= 1.5


@pytest.mark.asyncio
async def test_demo_adapter_warns_on_every_call(caplog: pytest.LogCaptureFixture) -> None:
    """Demo mode must log a WARNING on every generation so non-production output
    is never mistaken for real AI. The warning must carry no prompt content."""
    import logging

    adapter = DemoLLMAdapter()
    secret = "SECRET_PROMPT_CONTENT_XYZ"
    with caplog.at_level(logging.WARNING, logger="skylize"):
        await adapter.generate(_make_request(secret))

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "demo" in warnings[0].getMessage().lower()
    assert secret not in warnings[0].getMessage()


# ── Dispatch is on agent_id, exactly ────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("agent_id", sorted(_DEMO_RESPONSES))
async def test_each_agent_with_a_payload_gets_exactly_its_own(agent_id: str) -> None:
    """Every agent that HAS a canned payload receives that payload and no other.

    The old `_pick_response` sniffed keywords out of the system + user prompt,
    so what an agent received depended on the words in front of it rather than
    on who it was. This asserts identity-based dispatch for all seven.
    """
    adapter = DemoLLMAdapter()
    resp = await adapter.generate(_make_request("any prompt at all", agent_id=agent_id))
    assert json.loads(resp.text) == _DEMO_RESPONSES[agent_id]


@pytest.mark.asyncio
async def test_director_growth_gets_its_own_error_not_cfos_content() -> None:
    """The specific defect this replaced.

    `director_growth`'s own `agent_role` -- "Director Growth - proposes campaigns
    & budget reallocations" -- contains the word "budget", so the sniff's
    `if "budget" in combined` branch handed it `cfo_agent`'s budget summary
    UNCONDITIONALLY, on every call. That is another agent's output presented as
    this agent's, which no schema check downstream could catch as wrong content.
    It must now be a typed error naming director_growth, and it must not contain
    cfo_agent's payload.
    """
    contract = MVP_REGISTRY.resolve("director_growth")
    assert "budget" in contract.agent_role.lower(), (
        "the role string that used to trigger the mis-route changed; this test's "
        "premise needs rechecking"
    )

    adapter = DemoLLMAdapter()
    with pytest.raises(DemoResponseUnavailable) as exc_info:
        await adapter.generate(
            _make_request(
                "Propose a budget reallocation across the paid channels.",
                system=f"You are a {contract.agent_role}.",
                agent_id="director_growth",
            )
        )
    assert exc_info.value.agent_id == "director_growth"
    assert "director_growth" in str(exc_info.value)
    # Not cfo_agent's content, by any route.
    assert str(_DEMO_RESPONSES["cfo_agent"]["summary"]) not in str(exc_info.value)


@pytest.mark.asyncio
async def test_agent_without_a_payload_raises_the_typed_error() -> None:
    """No fallback. `_FALLBACK_RESPONSE` used to return
    `{"result": "[DEMO] Generated output ..."}`, which satisfies no agent's
    output schema -- so the failure surfaced later as an opaque 502 blaming the
    provider. It now fails here, naming the agent."""
    adapter = DemoLLMAdapter()
    with pytest.raises(DemoResponseUnavailable) as exc_info:
        await adapter.generate(_make_request("anything", agent_id=UNKNOWN_AGENT))
    assert exc_info.value.agent_id == UNKNOWN_AGENT
    assert UNKNOWN_AGENT in str(exc_info.value)
    # The message tells the operator which agents CAN be demoed.
    for demoable in _DEMO_RESPONSES:
        assert demoable in str(exc_info.value)


@pytest.mark.asyncio
async def test_prompt_content_can_no_longer_steer_dispatch() -> None:
    """Customer input used to change which agent's payload came back.

    The sniff read the user prompt, so an agent whose caller happened to write
    "hook" / "caption" / "budget" was routed to whichever payload matched first.
    Identity now wins over content: the same agent gets the same answer whatever
    the prompt says, and an agent with no payload still raises.
    """
    adapter = DemoLLMAdapter()
    steering = [
        "write me a hook",
        "rewrite this caption",
        "tighten the CTA",
        "cut the ad budget",
        "keyword research please",
        "draft the script",
    ]
    for prompt in steering:
        resp = await adapter.generate(_make_request(prompt, agent_id="ad_copy_agent"))
        assert json.loads(resp.text) == _DEMO_RESPONSES["ad_copy_agent"], prompt

        with pytest.raises(DemoResponseUnavailable):
            await adapter.generate(_make_request(prompt, agent_id=UNKNOWN_AGENT))
