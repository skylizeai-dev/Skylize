"""
DemoLLMAdapter — deterministic, template-based LLM for demo mode.

Used when SKYLIZE_ANTHROPIC_API_KEY is absent. Returns realistic-looking
output clearly marked [DEMO] so nobody mistakes it for real AI output.
Simulates latency (0.5–1.5 s) so the demo feels like a real generation.

DISPATCH IS EXACT, ON `agent_id`. A canned payload belongs to exactly one
agent; there is no fallback and no substitution. An agent with no entry in
`_DEMO_RESPONSES` raises `DemoResponseUnavailable` naming itself, because the
alternatives are both dishonest — another agent's payload is a wrong answer
wearing the right agent's name, and a generic stub satisfies no output schema.
Only 8 of the 22 registered agents have a payload; the other 14 cannot be
demoed, and demo mode now says which and why instead of failing downstream.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from pydantic import BaseModel

from .gateway import (
    LLMContentBlock,
    LLMGenerateRequest,
    LLMGenerateResponse,
    LLMGenerateWithToolsRequest,
    LLMMessage,
    LLMUsage,
)

if TYPE_CHECKING:
    from ...tools.base import ToolDefinition
    from .structured import StructuredRequest

log = logging.getLogger(__name__)

_TModel = TypeVar("_TModel", bound=BaseModel)

# Logged at WARNING on every demo generation so non-production output is never
# mistaken for real AI (the message carries no prompt content).
_DEMO_ACTIVE_WARNING = "demo_llm_adapter_active_returning_non_production_output"

_DEMO_HOOKS: list[str] = [
    "[DEMO] Stop everything — this is the last product you'll ever need.",
    "[DEMO] Everyone in your market is switching. Here's why.",
    "[DEMO] Warning: once you try this, there's no going back.",
    "[DEMO] The fastest way to get results without the usual frustration.",
    "[DEMO] What nobody tells you about this category (but should).",
]

#: Canned payloads, keyed by the EXACT `agent_id` the request carries. This is a
#: dispatch table, not a hint set: an agent gets this entry or it gets an error.
_DEMO_RESPONSES: dict[str, dict[str, object]] = {
    "hook_generator_agent": {"hooks": _DEMO_HOOKS},
    "ad_copy_agent": {"variants": [
        "[DEMO] Headline variant 1 — bold, benefit-led.",
        "[DEMO] Headline variant 2 — curiosity-gap open.",
        "[DEMO] Headline variant 3 — social proof angle.",
    ]},
    "caption_writer_agent": {"captions": [
        "[DEMO] Caption 1 — short and punchy for feed posts.",
        "[DEMO] Caption 2 — story-format with CTA.",
        "[DEMO] Caption 3 — question-hook to drive comments.",
    ]},
    "script_writer_agent": {
        "script": "[DEMO] 0:00 Hook — grab attention.\n0:05 Problem — agitate the pain.\n0:15 Solution — introduce the product.\n0:25 CTA — tell them what to do next.",
        "beats": ["Hook", "Problem", "Solution", "CTA"],
    },
    "cta_optimizer_agent": {"ctas": [
        "[DEMO] Get started free →",
        "[DEMO] Claim your spot →",
        "[DEMO] See it in action →",
    ]},
    "seo_keyword_agent": {
        "primary_keywords": [
            "[DEMO] best project management software",
            "[DEMO] project management tools for startups",
            "[DEMO] how to choose a pm tool",
        ],
        "keyword_difficulty_notes": (
            "[DEMO] Moderate-to-high difficulty on head terms — top-10 results are "
            "dominated by established review sites with strong backlink profiles. "
            "Long-tail, intent-specific terms are far more winnable near-term."
        ),
        "content_angle_suggestions": [
            "[DEMO] Comparison guide vs. the category leader",
            "[DEMO] Use-case deep dive for the target segment",
        ],
    },
    "cfo_agent": {
        "summary": "[DEMO] Spend for the period tracked close to plan with one concentration risk.",
        "total": 0.0,
        "flags": [],
        "recommendation": "[DEMO] Review the highest-concentration category before next cycle's allocation.",
    },
    # Not a registered agent — the internal work-journal brief summarizer
    # (edge/routes/brief.py), which also calls LLMGateway.generate() and so
    # also needs a demo-mode payload.
    "brief_summarizer": {
        "summary": "[DEMO] A few things happened since you last checked — nothing urgent.",
    },
    "cowork_agent": {
        "reply": "[DEMO] Understood — here is what I can do within your authority.",
    },
}

class DemoResponseUnavailable(Exception):
    """No canned demo payload exists for this `agent_id`.

    Demo mode can only produce output for the agents it has a hand-written,
    schema-valid payload for. Every other agent fails HERE, loudly, naming
    itself — rather than being handed some other agent's payload (a wrong
    answer presented as a right one) or a generic `{"result": ...}` stub (which
    satisfies no agent's output schema and resurfaces later as an opaque
    provider error). Neither substitute is honest: an agent with no demo
    payload cannot be demoed, and this says so at the point of failure.
    """

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        super().__init__(
            f"demo mode has no canned response for agent_id={agent_id!r}; the "
            f"{len(_DEMO_RESPONSES)} agents with a demo payload are "
            f"{', '.join(sorted(_DEMO_RESPONSES))}. Run this agent against a "
            "live provider key instead."
        )


def _pick_response(agent_id: str) -> dict[str, object]:
    """Exact lookup on the acting agent's id — never a guess.

    This used to sniff keywords out of the system + user prompt, which is how
    `director_growth` was served `cfo_agent`'s payload unconditionally: its
    `agent_role` string ("...campaigns & budget reallocations") matched
    `if "budget" in combined`. The sniff also read CUSTOMER content, so an
    agent's routing depended on the words in the caller's own input.

    `agent_id` is a REQUIRED field on every request model that reaches this
    adapter (gateway.py:165, gateway.py:202, structured.py:122), and every live
    construction site sources it from the contract or the validated
    GovernanceToken — so dispatching on it is exact and cannot be steered by
    input text.
    """
    try:
        return _DEMO_RESPONSES[agent_id]
    except KeyError:
        raise DemoResponseUnavailable(agent_id) from None


def _demo_input_for(schema: type[BaseModel]) -> dict[str, Any]:
    """Fill a plausible value for each required field of a tool's input_schema."""
    values: dict[str, Any] = {}
    for name, field in schema.model_fields.items():
        if not field.is_required():
            continue
        annotation = field.annotation
        if annotation is int:
            values[name] = 3
        elif annotation is float:
            values[name] = 1.0
        elif annotation is bool:
            values[name] = True
        else:
            values[name] = f"[DEMO] {name.replace('_', ' ')}"
    return values


def _has_tool_result(messages: list[LLMMessage]) -> bool:
    if not messages:
        return False
    last = messages[-1]
    return last.role == "user" and any(block.kind == "tool_result" for block in last.content)


class DemoLLMAdapter:
    """Deterministic template LLM — no API key required."""

    _PROVIDER = "demo"
    _MODEL = "demo-v1"

    async def generate(self, request: LLMGenerateRequest) -> LLMGenerateResponse:
        log.warning(_DEMO_ACTIVE_WARNING)
        await asyncio.sleep(random.uniform(0.5, 1.5))
        payload = _pick_response(request.agent_id)
        text = json.dumps(payload)
        fake_prompt_tokens = len(request.prompt) // 4
        fake_completion_tokens = len(text) // 4
        return LLMGenerateResponse(
            text=text,
            provider=self._PROVIDER,
            concrete_model=self._MODEL,
            usage=LLMUsage(
                prompt_tokens=fake_prompt_tokens,
                completion_tokens=fake_completion_tokens,
                total_tokens=fake_prompt_tokens + fake_completion_tokens,
            ),
            cost_usd_micros=0,
        )

    def generate_sync(self, request: LLMGenerateRequest) -> LLMGenerateResponse:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.generate(request))
        finally:
            loop.close()

    async def generate_with_tools(
        self, request: LLMGenerateWithToolsRequest, tools: list["ToolDefinition"]
    ) -> LLMGenerateResponse:
        """Simulate exactly one tool call, then a final templated answer.

        First turn (no tool_result in the transcript yet): proposes calling
        the first available tool with a plausible input, so the demo flow
        exercises the full loop UI/logging without an API key. Second turn
        (a tool_result is present): returns the same templated output as
        `generate()`.
        """
        log.warning(_DEMO_ACTIVE_WARNING)
        await asyncio.sleep(random.uniform(0.5, 1.5))

        if tools and not _has_tool_result(request.messages):
            tool = tools[0]
            block = LLMContentBlock(
                kind="tool_use",
                tool_use_id="demo_call_1",
                tool_name=tool.tool_id,
                tool_input=_demo_input_for(tool.input_schema),
            )
            fake_prompt_tokens = sum(
                len(b.text or "") for m in request.messages for b in m.content
            ) // 4 or 20
            return LLMGenerateResponse(
                text="",
                provider=self._PROVIDER,
                concrete_model=self._MODEL,
                usage=LLMUsage(
                    prompt_tokens=fake_prompt_tokens,
                    completion_tokens=15,
                    total_tokens=fake_prompt_tokens + 15,
                ),
                cost_usd_micros=0,
                stop_reason="tool_use",
                content=[block],
            )

        prompt = " ".join(
            block.text or ""
            for message in request.messages
            for block in message.content
            if block.kind == "text"
        )
        # The tool-loop request carries the same required `agent_id`
        # (gateway.py:202), so the final turn dispatches identically to the
        # single-shot path — no transcript is reconstructed to be sniffed.
        payload = _pick_response(request.agent_id)
        text = json.dumps(payload)
        fake_prompt_tokens = len(prompt) // 4
        fake_completion_tokens = len(text) // 4
        return LLMGenerateResponse(
            text=text,
            provider=self._PROVIDER,
            concrete_model=self._MODEL,
            usage=LLMUsage(
                prompt_tokens=fake_prompt_tokens,
                completion_tokens=fake_completion_tokens,
                total_tokens=fake_prompt_tokens + fake_completion_tokens,
            ),
            cost_usd_micros=0,
            stop_reason="end_turn",
            content=[LLMContentBlock(kind="text", text=text)],
        )

    async def generate_structured(
        self,
        request: StructuredRequest,
        schema: type[_TModel],
        *,
        correlation_id: UUID,
    ) -> _TModel:
        gen_req = request.to_generate_request()
        response = await self.generate(gen_req)
        parsed = json.loads(response.text)
        return schema.model_validate(parsed)
