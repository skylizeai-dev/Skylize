"""
AnthropicAdapter — live LLM backend via the Anthropic Python SDK.

Used when SKYLIZE_ANTHROPIC_API_KEY is present. Logical model names map to
concrete Anthropic model IDs via Settings (llm_model_default / fast /
reasoning). The adapter refuses over-budget calls BEFORE any provider egress,
wraps BOTH egress paths (generate + generate_with_tools) in one bounded retry
policy (Settings-driven: 429 honours Retry-After else jittered backoff → then
LLMRateLimited; 5xx jittered backoff → then LLMProviderUnavailable; 400 /
context overflow re-raised immediately; 401 fails closed with no key material
in the error, logs, or events), accounts cost in USD micros from Settings
prices, and optionally reports every generation to Langfuse and OpenTelemetry —
observability failures never fail the call, and prompt text is never logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

import anthropic
from pydantic import BaseModel

from .gateway import (
    LLMAuthenticationError,
    LLMContentBlock,
    LLMGenerateRequest,
    LLMGenerateResponse,
    LLMGenerateWithToolsRequest,
    LLMMessage,
    LLMModelNotPriced,
    LLMProviderUnavailable,
    LLMRateLimited,
    LLMUsage,
    TokenBudgetExceeded,
)

if TYPE_CHECKING:
    from ...dal.cost_ledger import CostLedgerDAL
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
        cost_ledger: "CostLedgerDAL | None" = None,
    ) -> None:
        self._settings = settings
        # Billing-grade cost ledger (ADR-0006). When wired (postgres backend),
        # every egress is price-gated BEFORE the SDK call and recorded AFTER
        # the provider responds. When None (memory backend / unit harnesses)
        # the adapter keeps the documented Settings-float fallback for
        # cost_usd_micros and records nothing.
        self._cost_ledger = cost_ledger
        self._api_key = api_key or str(getattr(settings, "anthropic_api_key", "") or "")
        # Empty string when unset; `_client_kwargs` omits base_url in that case so
        # the SDK falls back to its own default endpoint resolution (env + built-in).
        self._base_url = base_url or str(getattr(settings, "anthropic_base_url", "") or "")
        self._langfuse = langfuse_client
        self._tracer = tracer
        # Retry policy bounds (from Settings; no magic numbers in the helper body).
        self._retry_max_attempts = int(getattr(settings, "llm_retry_max_attempts", 3))
        self._retry_base_delay = float(getattr(settings, "llm_retry_base_delay_seconds", 1.0))
        self._retry_max_delay = float(getattr(settings, "llm_retry_max_delay_seconds", 30.0))
        self._retry_jitter = float(getattr(settings, "llm_retry_jitter_seconds", 0.5))
        # Strict logical -> concrete map; unknown logical names fail loudly so a
        # typo never silently lands on the wrong (priced) model.
        self._model_map: dict[str, str] = {
            "default": str(settings.llm_model_default),
            "fast": str(settings.llm_model_fast),
            "reasoning": str(settings.llm_model_reasoning),
        }
        # Explicit concrete-model-id -> (input_price, output_price) map for EXACT
        # cost keying. Built from the three configured models paired with their
        # price tier, keyed by the concrete id, so an unknown model id raises in
        # _estimate_cost instead of being mispriced by substring guessing.
        self._price_map: dict[str, tuple[float, float]] = {
            str(settings.llm_model_default): (
                float(getattr(settings, "llm_price_sonnet_in", 3.0)),
                float(getattr(settings, "llm_price_sonnet_out", 15.0)),
            ),
            str(settings.llm_model_fast): (
                float(getattr(settings, "llm_price_haiku_in", 0.80)),
                float(getattr(settings, "llm_price_haiku_out", 4.0)),
            ),
            str(settings.llm_model_reasoning): (
                float(getattr(settings, "llm_price_opus_in", 15.0)),
                float(getattr(settings, "llm_price_opus_out", 75.0)),
            ),
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
    def _check_budget(request: LLMGenerateRequest | LLMGenerateWithToolsRequest) -> None:
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

    async def _require_price(self, *, org_id: str, model_id: str) -> None:
        """PRE-CALL pricing gate (owner decision D1).

        When the cost ledger is wired, the concrete model must have an active
        model_pricing row BEFORE any provider egress — a pricing gap refuses
        the call with a typed error instead of producing an unrecordable
        (budget-cap-evading) charge. Without a ledger there is nothing to
        check against; the documented Settings-float fallback applies.
        """
        if self._cost_ledger is None:
            return
        from ...dal.cost_ledger import PricingNotFound

        try:
            await self._cost_ledger.resolve_price_for(
                org_id=org_id,
                provider=self._PROVIDER,
                model=model_id,
                occurred_at=datetime.now(timezone.utc),
            )
        except PricingNotFound as exc:
            raise LLMModelNotPriced(
                f"no model_pricing entry for concrete model {model_id!r}; "
                "refusing the call before egress (a pricing gap must not "
                "become a way to evade budget caps)"
            ) from exc

    @staticmethod
    def _parse_retry_after(exc: anthropic.APIStatusError) -> float | None:
        """The provider's Retry-After (seconds) if present and numeric, else None.

        Only the delta-seconds form is honoured; an HTTP-date value falls through
        to backoff (returns None). Never raises.
        """
        try:
            header = exc.response.headers.get("retry-after")
        except Exception:  # noqa: BLE001 — a malformed response must not break retry
            return None
        if header is None:
            return None
        try:
            value = float(header)
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    def _retry_delay(self, attempt: int, exc: anthropic.APIStatusError) -> float:
        """Seconds to sleep before the next attempt (1-indexed `attempt`).

        429 honours Retry-After when present (capped at the max delay); otherwise
        both 429 and 5xx use jittered exponential backoff bounded by the max delay.
        """
        if exc.response.status_code == 429:
            retry_after = self._parse_retry_after(exc)
            if retry_after is not None:
                return min(retry_after, self._retry_max_delay)
        backoff = min(self._retry_base_delay * (2.0 ** (attempt - 1)), self._retry_max_delay)
        return backoff + random.uniform(0.0, self._retry_jitter)

    async def _call_with_retry(self, invoke: Callable[[], Awaitable[Any]]) -> Any:
        """Retry an already-bound provider call uniformly across egress paths.

        Client-agnostic: `invoke` is a zero-argument callable returning a FRESH
        awaitable for the provider call. The sync egress (generate) binds it
        through asyncio.to_thread on anthropic.Anthropic; the async egress
        (generate_with_tools) binds anthropic.AsyncAnthropic.messages.create
        directly. Both paths share this one reliability wrapper regardless of
        client type, so the two egresses are wrapped identically.

        Policy (all bounds from Settings):
          * 401 — fail closed immediately as LLMAuthenticationError; the message
            carries no key material and the SDK exception is not chained.
          * 429 — honour Retry-After, else jittered exponential backoff; bounded
            attempts; then LLMRateLimited.
          * >=500 — jittered exponential backoff; bounded attempts; then
            LLMProviderUnavailable.
          * other 4xx (incl. 400 / context overflow) — re-raise the provider's
            typed error immediately, no retry.
        """
        last_exc: anthropic.APIStatusError | None = None
        for attempt in range(1, self._retry_max_attempts + 1):
            try:
                return await invoke()
            except anthropic.APIStatusError as exc:
                status = exc.response.status_code
                if status == 401:
                    # Fail closed. Static message, no chaining (`from None`) so no
                    # credential can reach the exception string, logs, or events.
                    raise LLMAuthenticationError(
                        "anthropic authentication failed (401)"
                    ) from None
                if status != 429 and status < 500:
                    # 400 / context overflow and other non-retryable 4xx.
                    raise
                last_exc = exc
                if attempt >= self._retry_max_attempts:
                    break
                await asyncio.sleep(self._retry_delay(attempt, exc))

        assert last_exc is not None  # loop only breaks after a retryable failure
        if last_exc.response.status_code == 429:
            raise LLMRateLimited(
                f"anthropic rate limited after {self._retry_max_attempts} attempts"
            ) from last_exc
        raise LLMProviderUnavailable(
            f"anthropic unavailable after {self._retry_max_attempts} attempts"
        ) from last_exc

    async def _settle_cost(
        self,
        *,
        request: LLMGenerateRequest | LLMGenerateWithToolsRequest,
        message: Any,
        prompt_tokens: int,
        completion_tokens: int,
        fallback_model_id: str,
    ) -> int:
        """Record the served call in the cost ledger; return its cost in micros.

        Called ONLY after a provider response was actually received, so the
        timeout / retry-exhausted / 401 paths can never write a row. With a
        wired ledger, ONE CostObservation is built from first-hand response
        data — the provider's RESOLVED model id (owner decision D3) and the
        provider message id as the idempotency key — and the SAME price
        resolution that lands on the ledger row prices the returned
        cost_usd_micros (owner decision D2). A ledger write failure is logged
        at ERROR with the correlation_id and re-raised: a call whose charge
        cannot be recorded must not be reported as a silent success.

        Without a ledger (memory backend / unit harnesses) the documented
        Settings-float fallback prices the response instead.
        """
        if self._cost_ledger is None:
            return self._estimate_cost(fallback_model_id, prompt_tokens, completion_tokens)

        from ...dal.cost_ledger import CostObservation

        occurred_at = datetime.now(timezone.utc)
        observation = CostObservation(
            org_id=request.org_id,
            correlation_id=request.correlation_id,
            agent_id=request.agent_id,
            run_id=request.governance_token_id,
            provider=self._PROVIDER,
            model=str(message.model),
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            occurred_at=occurred_at,
            billing_period=occurred_at.strftime("%Y-%m"),
            idempotency_key=str(message.id),
        )
        try:
            record = await self._cost_ledger.record_cost(observation)
        except Exception:
            log.error(
                "cost_ledger_write_failed correlation_id=%s model=%s idempotency_key=%s",
                request.correlation_id,
                observation.model,
                observation.idempotency_key,
            )
            raise
        return record.cost_micros

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
        await self._require_price(org_id=request.org_id, model_id=model_id)

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
                lambda: asyncio.to_thread(lambda: client.messages.create(**kwargs))
            )

            text = "".join(
                block.text
                for block in message.content
                if getattr(block, "type", None) == "text"
            )
            prompt_tokens = int(message.usage.input_tokens)
            completion_tokens = int(message.usage.output_tokens)
            cost_usd_micros = await self._settle_cost(
                request=request,
                message=message,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                fallback_model_id=model_id,
            )
            response = LLMGenerateResponse(
                text=text,
                provider=self._PROVIDER,
                concrete_model=model_id,
                usage=LLMUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                ),
                cost_usd_micros=cost_usd_micros,
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
        self._check_budget(request)
        model_id = self._concrete_model(request.model)
        await self._require_price(org_id=request.org_id, model_id=model_id)
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
        cost_usd_micros = await self._settle_cost(
            request=request,
            message=message,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            fallback_model_id=model_id,
        )
        return LLMGenerateResponse(
            text=text,
            provider=self._PROVIDER,
            concrete_model=model_id,
            usage=LLMUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            cost_usd_micros=cost_usd_micros,
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
        """DEMOTED FALLBACK (owner decision D2): Settings-float pricing.

        model_pricing (via the cost ledger) is the single source of truth for
        price. This float-based estimate remains ONLY for deployments with no
        ledger wired (memory backend / unit harnesses) and logs at WARNING on
        every use so two silent price sources can never coexist.
        """
        log.warning(
            "settings_price_fallback_used model=%s (no cost ledger wired; "
            "model_pricing is the price source of truth when available)",
            model_id,
        )
        try:
            in_price, out_price = self._price_map[model_id]
        except KeyError:
            raise LLMModelNotPriced(
                f"no price entry for concrete model {model_id!r}; "
                f"configured models: {sorted(self._price_map)}"
            ) from None
        cost_usd = (
            (prompt_tokens / 1_000_000) * in_price
            + (completion_tokens / 1_000_000) * out_price
        )
        return int(cost_usd * 1_000_000)
