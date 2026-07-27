"""
AnthropicAdapter — live LLM backend via the Anthropic Python SDK.

Used when SKYLIZE_ANTHROPIC_API_KEY is present. Logical model names map to
concrete Anthropic model IDs via Settings (llm_model_default / fast /
reasoning). The adapter refuses over-budget calls BEFORE any provider egress,
retries a 5xx once (1s pause) then raises LLMProviderUnavailable, accounts
cost in USD micros from Settings prices, and optionally reports every
generation to Langfuse and OpenTelemetry — observability failures never fail
the call, and prompt text is never logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

import anthropic
from pydantic import BaseModel

from .gateway import (
    LLMContentBlock,
    LLMGenerateRequest,
    LLMGenerateResponse,
    LLMGenerateWithToolsRequest,
    LLMMessage,
    LLMProviderUnavailable,
    LLMUsage,
    TokenBudgetExceeded,
)

if TYPE_CHECKING:
    from ...tools.base import ToolDefinition
    from .structured import StructuredRequest

log = logging.getLogger(__name__)

_TModel = TypeVar("_TModel", bound=BaseModel)


def _sanitize_tool_name(tool_id: str) -> str:
    """Anthropic tool `name` must match ``^[a-zA-Z0-9_-]{1,128}$`` — no dots."""
    return tool_id.replace(".", "_")


def _to_anthropic_tools(
    tools: list["ToolDefinition"],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Build the Anthropic `tools` payload + a sanitized-name -> tool_id map.

    The map lets `_normalize_anthropic_message` restore the original (dotted)
    `tool_id` when parsing a `tool_use` block back — callers never see the
    sanitized name.
    """
    anthropic_tools: list[dict[str, Any]] = []
    name_map: dict[str, str] = {}
    for tool in tools:
        sanitized = _sanitize_tool_name(tool.tool_id)
        if sanitized in name_map:
            raise ValueError(f"tool name collision after sanitization: {sanitized!r}")
        name_map[sanitized] = tool.tool_id
        anthropic_tools.append(
            {
                "name": sanitized,
                "description": tool.description,
                "input_schema": tool.input_schema.model_json_schema(),
            }
        )
    return anthropic_tools, name_map


def _to_anthropic_message(message: LLMMessage) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for block in message.content:
        if block.kind == "text":
            content.append({"type": "text", "text": block.text or ""})
        elif block.kind == "tool_use":
            content.append(
                {
                    "type": "tool_use",
                    "id": block.tool_use_id,
                    "name": _sanitize_tool_name(block.tool_name or ""),
                    "input": block.tool_input or {},
                }
            )
        elif block.kind == "tool_result":
            result_block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": block.tool_use_id,
                "content": block.tool_output or "",
            }
            if block.is_error:
                result_block["is_error"] = True
            content.append(result_block)
    return {"role": message.role, "content": content}


def _normalize_anthropic_message(
    message: Any, *, name_map: dict[str, str]
) -> tuple[str, list[LLMContentBlock]]:
    """Anthropic `Message.content` blocks -> our provider-neutral shape.

    `tool_use.name` is mapped back through `name_map` to the original
    (dotted) `tool_id` so nothing downstream ever sees the sanitized form.
    """
    text_parts: list[str] = []
    blocks: list[LLMContentBlock] = []
    for raw_block in message.content:
        block_type = getattr(raw_block, "type", None)
        if block_type == "text":
            text_parts.append(raw_block.text)
            blocks.append(LLMContentBlock(kind="text", text=raw_block.text))
        elif block_type == "tool_use":
            internal_id = name_map.get(raw_block.name, raw_block.name)
            blocks.append(
                LLMContentBlock(
                    kind="tool_use",
                    tool_use_id=raw_block.id,
                    tool_name=internal_id,
                    tool_input=dict(raw_block.input),
                )
            )
    return "".join(text_parts), blocks


class AnthropicAdapter:
    """Live Anthropic Claude adapter. Requires `pip install anthropic`."""

    _PROVIDER = "anthropic"

    def __init__(
        self,
        settings: Any,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        langfuse_client: Any = None,
        tracer: Any = None,
    ) -> None:
        self._settings = settings
        self._api_key = api_key or str(getattr(settings, "anthropic_api_key", "") or "")
        # Empty string when unset; `_client_kwargs` omits base_url in that case so
        # the SDK falls back to its own default endpoint resolution (env + built-in).
        self._base_url = base_url or str(getattr(settings, "anthropic_base_url", "") or "")
        self._langfuse = langfuse_client
        self._tracer = tracer
        # Strict logical -> concrete map; unknown logical names fail loudly so a
        # typo never silently lands on the wrong (priced) model.
        self._model_map: dict[str, str] = {
            "default": str(settings.llm_model_default),
            "fast": str(settings.llm_model_fast),
            "reasoning": str(settings.llm_model_reasoning),
        }

    # -- helpers --------------------------------------------------------------

    def _client_kwargs(self) -> dict[str, Any]:
        """Constructor kwargs shared by both egress clients (sync + async).

        base_url is included ONLY when configured; when unset the argument is
        omitted entirely so the Anthropic SDK applies its own default endpoint
        (rather than being handed an explicit None).
        """
        kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return kwargs

    def _concrete_model(self, logical: str) -> str:
        try:
            return self._model_map[logical]
        except KeyError:
            raise ValueError(
                f"unknown logical model {logical!r}; expected one of {sorted(self._model_map)}"
            ) from None

    @staticmethod
    def _check_budget(request: LLMGenerateRequest) -> None:
        """Refuse before egress when the request cannot fit the remaining budget."""
        if request.max_token_budget is None:
            return
        remaining = request.max_token_budget - (request.tokens_used_so_far or 0)
        if request.requested_max_tokens > remaining:
            raise TokenBudgetExceeded(
                f"requested_max_tokens={request.requested_max_tokens} exceeds "
                f"remaining budget={remaining} "
                f"(max_token_budget={request.max_token_budget}, "
                f"tokens_used_so_far={request.tokens_used_so_far or 0})"
            )

    async def _call_with_retry(self, invoke: Callable[[], Awaitable[Any]]) -> Any:
        """One retry on 5xx after a 1s pause; 4xx raises immediately.

        Client-agnostic: `invoke` is a zero-argument callable returning a FRESH
        awaitable for the provider call. The sync egress (generate) binds it
        through asyncio.to_thread on anthropic.Anthropic; the async egress
        (generate_with_tools) binds anthropic.AsyncAnthropic.messages.create
        directly. Both paths share this one reliability wrapper regardless of
        client type, so the two egresses are wrapped identically.
        """
        try:
            return await invoke()
        except anthropic.APIStatusError as exc:
            if exc.response.status_code < 500:
                raise
            await asyncio.sleep(1)
            try:
                return await invoke()
            except anthropic.APIStatusError as retry_exc:
                if retry_exc.response.status_code < 500:
                    raise
                raise LLMProviderUnavailable(
                    f"anthropic unavailable after retry: {retry_exc}"
                ) from retry_exc

    def _record_langfuse(
        self, request: LLMGenerateRequest, model_id: str, response: LLMGenerateResponse
    ) -> None:
        """Report the generation to Langfuse; failures never fail the call."""
        if self._langfuse is None:
            return
        try:
            trace = self._langfuse.trace(
                id=str(request.governance_token_id),
                name="llm.generate",
                metadata={"org_id": request.org_id, "provider": self._PROVIDER},
            )
            trace.generation(
                name="anthropic.messages.create",
                model=model_id,
                metadata={
                    "governance_token_id": str(request.governance_token_id),
                    "org_id": request.org_id,
                    "concrete_model": model_id,
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "cost_usd_micros": response.cost_usd_micros,
                },
            )
        except Exception:  # noqa: BLE001 — observability must never fail the call
            log.warning("langfuse_record_failed", exc_info=True)

    # -- LLMGateway interface ---------------------------------------------------

    async def generate(self, request: LLMGenerateRequest) -> LLMGenerateResponse:
        self._check_budget(request)
        model_id = self._concrete_model(request.model)

        span = self._tracer.start_span("llm.generate") if self._tracer is not None else None
        if span is not None:
            span.set_attribute("provider", self._PROVIDER)
            span.set_attribute("org_id", request.org_id)
            span.set_attribute("model", model_id)
        try:
            kwargs: dict[str, Any] = dict(
                model=model_id,
                max_tokens=request.requested_max_tokens,
                temperature=request.temperature,
                messages=[{"role": "user", "content": request.prompt}],
            )
            if request.system:
                kwargs["system"] = request.system

            client = anthropic.Anthropic(**self._client_kwargs())
            message = await self._call_with_retry(
                lambda: asyncio.to_thread(client.messages.create, **kwargs)
            )

            text = "".join(
                block.text
                for block in message.content
                if getattr(block, "type", None) == "text"
            )
            prompt_tokens = int(message.usage.input_tokens)
            completion_tokens = int(message.usage.output_tokens)
            response = LLMGenerateResponse(
                text=text,
                provider=self._PROVIDER,
                concrete_model=model_id,
                usage=LLMUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                ),
                cost_usd_micros=self._estimate_cost(model_id, prompt_tokens, completion_tokens),
            )
            self._record_langfuse(request, model_id, response)
            return response
        finally:
            if span is not None:
                span.end()

    def generate_sync(self, request: LLMGenerateRequest) -> LLMGenerateResponse:
        """Synchronous convenience wrapper — must not be called from a running loop."""
        return asyncio.run(self.generate(request))

    async def generate_with_tools(
        self, request: LLMGenerateWithToolsRequest, tools: list["ToolDefinition"]
    ) -> LLMGenerateResponse:
        model_id = self._concrete_model(request.model)
        anthropic_tools, name_map = _to_anthropic_tools(tools)
        kwargs: dict[str, Any] = dict(
            model=model_id,
            max_tokens=request.requested_max_tokens,
            temperature=request.temperature,
            messages=[_to_anthropic_message(m) for m in request.messages],
            tools=anthropic_tools,
        )
        if request.system:
            kwargs["system"] = request.system

        client = anthropic.AsyncAnthropic(**self._client_kwargs())
        message = await self._call_with_retry(lambda: client.messages.create(**kwargs))
        text, blocks = _normalize_anthropic_message(message, name_map=name_map)
        prompt_tokens = int(message.usage.input_tokens)
        completion_tokens = int(message.usage.output_tokens)
        return LLMGenerateResponse(
            text=text,
            provider=self._PROVIDER,
            concrete_model=model_id,
            usage=LLMUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            cost_usd_micros=self._estimate_cost(model_id, prompt_tokens, completion_tokens),
            stop_reason=message.stop_reason,
            content=blocks,
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

    def _estimate_cost(self, model_id: str, prompt_tokens: int, completion_tokens: int) -> int:
        settings = self._settings
        if "haiku" in model_id:
            in_price = float(getattr(settings, "llm_price_haiku_in", 0.80))
            out_price = float(getattr(settings, "llm_price_haiku_out", 4.0))
        elif "opus" in model_id:
            in_price = float(getattr(settings, "llm_price_opus_in", 15.0))
            out_price = float(getattr(settings, "llm_price_opus_out", 75.0))
        else:
            in_price = float(getattr(settings, "llm_price_sonnet_in", 3.0))
            out_price = float(getattr(settings, "llm_price_sonnet_out", 15.0))
        cost_usd = (
            (prompt_tokens / 1_000_000) * in_price
            + (completion_tokens / 1_000_000) * out_price
        )
        return int(cost_usd * 1_000_000)
