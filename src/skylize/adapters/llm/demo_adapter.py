"""
DemoLLMAdapter — deterministic, template-based LLM for demo mode.

Used when SKYLIZE_ANTHROPIC_API_KEY is absent. Returns realistic-looking
output clearly marked [DEMO] so nobody mistakes it for real AI output.
Simulates latency (0.5–1.5 s) so the demo feels like a real generation.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
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

_TModel = TypeVar("_TModel", bound=BaseModel)

_DEMO_HOOKS: list[str] = [
    "[DEMO] Stop everything — this is the last product you'll ever need.",
    "[DEMO] Everyone in your market is switching. Here's why.",
    "[DEMO] Warning: once you try this, there's no going back.",
    "[DEMO] The fastest way to get results without the usual frustration.",
    "[DEMO] What nobody tells you about this category (but should).",
]

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
}

_FALLBACK_RESPONSE: dict[str, object] = {"result": "[DEMO] Generated output — replace with live model."}


def _pick_response(request: LLMGenerateRequest) -> dict[str, object]:
    system = request.system or ""
    prompt = request.prompt
    combined = (system + " " + prompt).lower()
    for agent_id, payload in _DEMO_RESPONSES.items():
        if agent_id in combined:
            return payload
    # Try to detect from prompt keywords
    if "hook" in combined:
        return _DEMO_RESPONSES["hook_generator_agent"]
    if "caption" in combined:
        return _DEMO_RESPONSES["caption_writer_agent"]
    if re.search(r"\bscript\b", combined):  # word-boundary: "product_description" is not a match
        return _DEMO_RESPONSES["script_writer_agent"]
    if "cta" in combined or "call to action" in combined:
        return _DEMO_RESPONSES["cta_optimizer_agent"]
    if "ad copy" in combined or "variant" in combined:
        return _DEMO_RESPONSES["ad_copy_agent"]
    if "keyword" in combined or "serp" in combined:
        return _DEMO_RESPONSES["seo_keyword_agent"]
    if "budget" in combined or "line_items" in combined:
        return _DEMO_RESPONSES["cfo_agent"]
    return _FALLBACK_RESPONSE


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
        await asyncio.sleep(random.uniform(0.5, 1.5))
        payload = _pick_response(request)
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
        fake_request = LLMGenerateRequest(
            model=request.model,
            prompt=prompt,
            system=request.system,
            requested_max_tokens=request.requested_max_tokens,
            temperature=request.temperature,
            governance_token_id=request.governance_token_id,
            org_id=request.org_id,
        )
        payload = _pick_response(fake_request)
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
