"""Unit tests for AnthropicAdapter: budget enforcement, retry, normalization, cost, OTel, Langfuse."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import anthropic
import httpx
import pytest

from skylize.adapters.llm.anthropic_adapter import AnthropicAdapter
from skylize.adapters.llm.gateway import (
    LLMContentBlock,
    LLMGenerateRequest,
    LLMGenerateResponse,
    LLMGenerateWithToolsRequest,
    LLMMessage,
    LLMProviderUnavailable,
    TokenBudgetExceeded,
)
from skylize.config import Settings

ORG = "org_test"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "anthropic_api_key": "sk-test",
        "llm_model_default": "claude-sonnet-4-6",
        "llm_model_fast": "claude-haiku-4-5-20251001",
        "llm_model_reasoning": "claude-opus-4-6",
        "llm_price_sonnet_in": 3.0,
        "llm_price_sonnet_out": 15.0,
        "llm_price_haiku_in": 0.80,
        "llm_price_haiku_out": 4.0,
        "llm_price_opus_in": 15.0,
        "llm_price_opus_out": 75.0,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _request(**kwargs: object) -> LLMGenerateRequest:
    defaults: dict[str, object] = {
        "model": "default",
        "prompt": "Hello",
        "requested_max_tokens": 100,
        "governance_token_id": uuid4(),
        "org_id": ORG,
    }
    defaults.update(kwargs)
    return LLMGenerateRequest(**defaults)  # type: ignore[arg-type]


def _mock_anthropic_response(
    text: str = "World", input_tokens: int = 10, output_tokens: int = 20
) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    resp.usage.input_tokens = input_tokens
    resp.usage.output_tokens = output_tokens
    return resp


def _make_adapter(
    langfuse_client: object = None,
    tracer: object = None,
    **settings_overrides: object,
) -> AnthropicAdapter:
    return AnthropicAdapter(
        settings=_settings(**settings_overrides),
        langfuse_client=langfuse_client,
        tracer=tracer,
    )


def _mock_tools_response(
    text: str = "World",
    input_tokens: int = 10,
    output_tokens: int = 20,
    stop_reason: str = "end_turn",
) -> MagicMock:
    """A response mock for the generate_with_tools (async) egress path.

    Unlike `_mock_anthropic_response`, this also carries `stop_reason` (a str,
    not a MagicMock) so the tool-path response model validates.
    """
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    resp.usage.input_tokens = input_tokens
    resp.usage.output_tokens = output_tokens
    resp.stop_reason = stop_reason
    return resp


def _tools_request(**kwargs: object) -> LLMGenerateWithToolsRequest:
    defaults: dict[str, object] = {
        "model": "default",
        "messages": [LLMMessage(role="user", content=[LLMContentBlock(kind="text", text="hi")])],
        "requested_max_tokens": 100,
        "governance_token_id": uuid4(),
        "org_id": ORG,
    }
    defaults.update(kwargs)
    return LLMGenerateWithToolsRequest(**defaults)  # type: ignore[arg-type]


def _httpx_response(status: int) -> httpx.Response:
    dummy = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx.Response(status, request=dummy)


def _patch_sync_client(adapter: AnthropicAdapter, side_effect: object) -> MagicMock:
    mock_create = MagicMock(side_effect=side_effect)
    mock_messages = MagicMock()
    mock_messages.create = mock_create
    mock_client = MagicMock()
    mock_client.messages = mock_messages
    return mock_client, mock_create


# ---------------------------------------------------------------------------
# Budget enforcement
# ---------------------------------------------------------------------------


async def test_budget_exceeded_raises_before_api_call() -> None:
    adapter = _make_adapter()
    req = _request(
        requested_max_tokens=500,
        max_token_budget=1000,
        tokens_used_so_far=600,  # remaining=400 < 500
    )
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        with pytest.raises(TokenBudgetExceeded):
            await adapter.generate(req)
        mock_cls.assert_not_called()


async def test_budget_within_limit_does_not_raise() -> None:
    adapter = _make_adapter()
    req = _request(
        requested_max_tokens=100,
        max_token_budget=1000,
        tokens_used_so_far=500,  # remaining=500 >= 100
    )
    mock_resp = _mock_anthropic_response()
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        result = await adapter.generate(req)
    assert isinstance(result, LLMGenerateResponse)


async def test_no_budget_fields_skips_check() -> None:
    adapter = _make_adapter()
    req = _request(requested_max_tokens=9999)
    mock_resp = _mock_anthropic_response()
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        result = await adapter.generate(req)
    assert result.text == "World"


# ---------------------------------------------------------------------------
# Unknown model
# ---------------------------------------------------------------------------


async def test_unknown_model_raises_value_error() -> None:
    adapter = _make_adapter()
    req = _request(model="turbo-9000")
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic"):
        with pytest.raises(ValueError, match="unknown logical model"):
            await adapter.generate(req)


# ---------------------------------------------------------------------------
# Retry logic — 500 → 1s wait → retry once
# ---------------------------------------------------------------------------


async def test_500_retries_once_then_succeeds() -> None:
    adapter = _make_adapter()
    req = _request()

    error_500 = anthropic.APIStatusError(
        message="internal server error",
        response=_httpx_response(500),
        body={},
    )
    mock_resp = _mock_anthropic_response()

    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.side_effect = [error_500, mock_resp]
        with patch("skylize.adapters.llm.anthropic_adapter.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await adapter.generate(req)

    assert result.text == "World"
    mock_sleep.assert_called_once_with(1)


async def test_500_retry_also_fails_raises_provider_unavailable() -> None:
    adapter = _make_adapter()
    req = _request()

    error_500 = anthropic.APIStatusError(
        message="internal server error",
        response=_httpx_response(500),
        body={},
    )

    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.side_effect = [error_500, error_500]
        with patch("skylize.adapters.llm.anthropic_adapter.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(LLMProviderUnavailable):
                await adapter.generate(req)


async def test_non_retryable_400_raises_immediately_no_retry() -> None:
    adapter = _make_adapter()
    req = _request()

    error_400 = anthropic.BadRequestError(
        message="bad request",
        response=_httpx_response(400),
        body={},
    )

    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.side_effect = [error_400]
        with pytest.raises(anthropic.BadRequestError):
            await adapter.generate(req)
        # Only 1 call — no retry
        assert mock_cls.return_value.messages.create.call_count == 1


async def test_generate_with_tools_retries_500_once_then_succeeds() -> None:
    """The async (tools) egress is routed through the SAME retry helper as the
    sync egress, so a 5xx is retried once there too (both wrapped identically)."""
    adapter = _make_adapter()
    req = _tools_request()

    error_500 = anthropic.APIStatusError(
        message="internal server error",
        response=_httpx_response(500),
        body={},
    )
    mock_create = AsyncMock(side_effect=[error_500, _mock_tools_response()])

    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value.messages.create = mock_create
        with patch(
            "skylize.adapters.llm.anthropic_adapter.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            result = await adapter.generate_with_tools(req, tools=[])

    assert result.text == "World"
    assert mock_create.call_count == 2
    mock_sleep.assert_called_once_with(1)


# ---------------------------------------------------------------------------
# Response normalization
# ---------------------------------------------------------------------------


async def test_normalization_logical_model_maps_to_concrete() -> None:
    adapter = _make_adapter()
    for logical in ("default", "fast", "reasoning"):
        req = _request(model=logical)
        mock_resp = _mock_anthropic_response()
        with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = mock_resp
            result = await adapter.generate(req)
        assert result.concrete_model == adapter._model_map[logical]
        assert result.provider == "anthropic"


async def test_normalization_usage_fields() -> None:
    adapter = _make_adapter()
    req = _request()
    mock_resp = _mock_anthropic_response(input_tokens=50, output_tokens=100)
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        result = await adapter.generate(req)
    assert result.usage.prompt_tokens == 50
    assert result.usage.completion_tokens == 100
    assert result.usage.total_tokens == 150


async def test_normalization_cost_usd_micros_nonzero() -> None:
    adapter = _make_adapter()
    req = _request(model="default")
    mock_resp = _mock_anthropic_response(input_tokens=1_000_000, output_tokens=1_000_000)
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        result = await adapter.generate(req)
    # sonnet: $3/M in + $15/M out → $18 → 18_000_000 micros
    assert result.cost_usd_micros == 18_000_000


async def test_system_prompt_passed_to_api() -> None:
    adapter = _make_adapter()
    req = _request(system="You are helpful.")
    mock_resp = _mock_anthropic_response()
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        await adapter.generate(req)
    call_kwargs = mock_cls.return_value.messages.create.call_args.kwargs
    assert call_kwargs["system"] == "You are helpful."


async def test_no_system_prompt_omits_key() -> None:
    adapter = _make_adapter()
    req = _request(system=None)
    mock_resp = _mock_anthropic_response()
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        await adapter.generate(req)
    call_kwargs = mock_cls.return_value.messages.create.call_args.kwargs
    assert "system" not in call_kwargs


# ---------------------------------------------------------------------------
# generate_sync wraps generate
# ---------------------------------------------------------------------------


def test_generate_sync_returns_response() -> None:
    adapter = _make_adapter()
    req = _request()
    mock_resp = _mock_anthropic_response(text="sync result")
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        result = adapter.generate_sync(req)
    assert result.text == "sync result"
    assert isinstance(result, LLMGenerateResponse)


# ---------------------------------------------------------------------------
# Langfuse span metadata
# ---------------------------------------------------------------------------


async def test_langfuse_span_metadata_correct() -> None:
    mock_trace = MagicMock()
    mock_langfuse = MagicMock()
    mock_langfuse.trace.return_value = mock_trace

    adapter = _make_adapter(langfuse_client=mock_langfuse)
    req = _request()
    mock_resp = _mock_anthropic_response()

    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        await adapter.generate(req)

    mock_langfuse.trace.assert_called_once()
    trace_kwargs = mock_langfuse.trace.call_args.kwargs
    assert trace_kwargs["id"] == str(req.governance_token_id)
    assert trace_kwargs["metadata"]["org_id"] == ORG

    mock_trace.generation.assert_called_once()
    gen_kwargs = mock_trace.generation.call_args.kwargs
    assert gen_kwargs["metadata"]["governance_token_id"] == str(req.governance_token_id)
    assert gen_kwargs["metadata"]["org_id"] == ORG
    assert gen_kwargs["metadata"]["concrete_model"] == adapter._model_map["default"]


async def test_no_cost_recording_without_langfuse() -> None:
    adapter = _make_adapter()  # no langfuse
    assert adapter._langfuse is None
    req = _request()
    mock_resp = _mock_anthropic_response()
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        result = await adapter.generate(req)
    assert result.cost_usd_micros >= 0


async def test_langfuse_error_does_not_propagate() -> None:
    mock_trace = MagicMock()
    mock_trace.generation.side_effect = RuntimeError("langfuse down")
    mock_langfuse = MagicMock()
    mock_langfuse.trace.return_value = mock_trace

    adapter = _make_adapter(langfuse_client=mock_langfuse)
    req = _request()
    mock_resp = _mock_anthropic_response()

    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        result = await adapter.generate(req)
    assert result.text == "World"


# ---------------------------------------------------------------------------
# Prompt NOT in logs
# ---------------------------------------------------------------------------


async def test_prompt_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    import logging
    adapter = _make_adapter()
    secret_prompt = "SUPER_SECRET_PROMPT_XYZ"
    req = _request(prompt=secret_prompt)
    mock_resp = _mock_anthropic_response()

    with caplog.at_level(logging.DEBUG, logger="skylize"):
        with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = mock_resp
            await adapter.generate(req)

    for record in caplog.records:
        assert secret_prompt not in record.getMessage()


# ---------------------------------------------------------------------------
# OTel tracer
# ---------------------------------------------------------------------------


async def test_otel_span_opened_and_closed() -> None:
    mock_span = MagicMock()
    mock_tracer = MagicMock()
    mock_tracer.start_span.return_value = mock_span

    adapter = _make_adapter(tracer=mock_tracer)
    req = _request()
    mock_resp = _mock_anthropic_response()

    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        await adapter.generate(req)

    mock_tracer.start_span.assert_called_once_with("llm.generate")
    mock_span.set_attribute.assert_any_call("provider", "anthropic")
    mock_span.set_attribute.assert_any_call("org_id", ORG)
    mock_span.end.assert_called_once()


async def test_no_tracer_does_not_raise() -> None:
    adapter = _make_adapter(tracer=None)
    req = _request()
    mock_resp = _mock_anthropic_response()
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        result = await adapter.generate(req)
    assert isinstance(result, LLMGenerateResponse)


# ---------------------------------------------------------------------------
# base_url override — passed at BOTH construction sites, omitted when unset
# ---------------------------------------------------------------------------


async def test_base_url_passed_to_sync_client_when_set() -> None:
    adapter = _make_adapter(anthropic_base_url="https://proxy.internal/v1")
    req = _request()
    mock_resp = _mock_anthropic_response()
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        await adapter.generate(req)
    assert mock_cls.call_args.kwargs.get("base_url") == "https://proxy.internal/v1"


async def test_base_url_omitted_from_sync_client_when_unset() -> None:
    adapter = _make_adapter()  # anthropic_base_url unset (None default)
    req = _request()
    mock_resp = _mock_anthropic_response()
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        await adapter.generate(req)
    # Omitted entirely — never handed to the SDK as an explicit None.
    assert "base_url" not in mock_cls.call_args.kwargs


async def test_base_url_passed_to_async_client_when_set() -> None:
    adapter = _make_adapter(anthropic_base_url="https://proxy.internal/v1")
    req = _tools_request()
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value.messages.create = AsyncMock(return_value=_mock_tools_response())
        await adapter.generate_with_tools(req, tools=[])
    assert mock_cls.call_args.kwargs.get("base_url") == "https://proxy.internal/v1"
