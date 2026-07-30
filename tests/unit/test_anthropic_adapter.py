"""Unit tests for AnthropicAdapter: budget enforcement, retry, normalization, cost, OTel, Langfuse."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from skylize.adapters.llm.anthropic_adapter import AnthropicAdapter
from skylize.adapters.llm.gateway import (
    LLMContentBlock,
    LLMGenerateRequest,
    LLMGenerateResponse,
    LLMGenerateWithToolsRequest,
    LLMMessage,
    LLMModelNotPriced,
    LLMProviderUnavailable,
    TokenBudgetExceeded,
)
from skylize.config import Settings

ORG = "org_test"

#: Sentinel so a test can pass cost_ledger=None to assert the no-price refusal.
_UNSET = object()


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "anthropic_api_key": "sk-test",
        "llm_model_default": "claude-sonnet-4-6",
        "llm_model_fast": "claude-haiku-4-5-20251001",
        "llm_model_reasoning": "claude-opus-4-6",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _fake_cost_ledger(
    *, input_micros: int = 3_000_000, output_micros: int = 15_000_000
) -> MagicMock:
    """A ledger that resolves a price and swallows the write.

    Required now, not optional: the adapter refuses a call before egress when no
    ledger is wired, because a deployment with no ledger has no price source and
    guessing one produces a wrong charge. Cases that assert on transport,
    tracing, or client construction still need a call to SUCCEED, so they need a
    price to exist. The figures are the harness's own, not a fallback: nothing in
    src/ supplies a default price any more.
    """
    from skylize.dal.cost_ledger import CostRecord, PriceSnapshot

    ledger = MagicMock()
    ledger.resolve_price_for = AsyncMock(
        return_value=PriceSnapshot(
            input_price_micros_per_mtok=input_micros,
            output_price_micros_per_mtok=output_micros,
            pricing_version=1,
            currency="USD",
        )
    )
    async def _record(observation: object, *, price: object) -> CostRecord:
        # Mirror the DAL's arithmetic so `cost_usd_micros` on the response traces
        # to the SNAPSHOT, not to a constant the fake invented.
        micros = (
            observation.input_tokens * price.input_price_micros_per_mtok  # type: ignore[attr-defined]
            + observation.output_tokens * price.output_price_micros_per_mtok  # type: ignore[attr-defined]
        ) // 1_000_000
        return CostRecord(
            entry_id=uuid4(), cost_micros=micros, currency="USD", inserted=True
        )

    ledger.record_cost = AsyncMock(side_effect=_record)
    return ledger


def _request(**kwargs: object) -> LLMGenerateRequest:
    defaults: dict[str, object] = {
        "model": "default",
        "prompt": "Hello",
        "requested_max_tokens": 100,
        "governance_token_id": uuid4(),
        "org_id": ORG,
        "correlation_id": uuid4(),
        "agent_id": "agent_test",
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
    cost_ledger: object = _UNSET,
    **settings_overrides: object,
) -> AnthropicAdapter:
    return AnthropicAdapter(
        settings=_settings(**settings_overrides),
        langfuse_client=langfuse_client,
        tracer=tracer,
        cost_ledger=_fake_cost_ledger() if cost_ledger is _UNSET else cost_ledger,
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
        "correlation_id": uuid4(),
        "agent_id": "agent_test",
    }
    defaults.update(kwargs)
    return LLMGenerateWithToolsRequest(**defaults)  # type: ignore[arg-type]


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


async def test_tools_budget_exceeded_raises_before_api_call() -> None:
    """generate_with_tools enforces the same pre-egress budget guard as generate."""
    adapter = _make_adapter()
    req = _tools_request(
        requested_max_tokens=500,
        max_token_budget=1000,
        tokens_used_so_far=600,  # remaining=400 < 500
    )
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.AsyncAnthropic") as mock_cls:
        with pytest.raises(TokenBudgetExceeded):
            await adapter.generate_with_tools(req, tools=[])
        mock_cls.assert_not_called()


async def test_tools_budget_within_limit_does_not_raise() -> None:
    adapter = _make_adapter()
    req = _tools_request(
        requested_max_tokens=100,
        max_token_budget=1000,
        tokens_used_so_far=500,  # remaining=500 >= 100
    )
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value.messages.create = AsyncMock(return_value=_mock_tools_response())
        result = await adapter.generate_with_tools(req, tools=[])
    assert isinstance(result, LLMGenerateResponse)


async def test_tools_no_budget_fields_skips_check() -> None:
    adapter = _make_adapter()
    req = _tools_request(requested_max_tokens=9999)
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value.messages.create = AsyncMock(return_value=_mock_tools_response())
        result = await adapter.generate_with_tools(req, tools=[])
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
    """The response's cost comes from the resolved model_pricing snapshot.

    Rewritten: this used to assert the Settings-float arithmetic ($3/M in +
    $15/M out) that the adapter no longer performs. The figure now traces to the
    price the ledger resolved, which is the only price source there is.
    """
    adapter = _make_adapter(
        cost_ledger=_fake_cost_ledger(input_micros=3_000_000, output_micros=15_000_000)
    )
    req = _request(model="default")
    mock_resp = _mock_anthropic_response(input_tokens=1_000_000, output_tokens=1_000_000)
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        result = await adapter.generate(req)
    # 1M in at 3_000_000 micros/MTok + 1M out at 15_000_000 = 18_000_000 micros.
    assert result.cost_usd_micros == 18_000_000


async def test_price_comes_from_the_resolved_snapshot_not_a_default() -> None:
    """Replaces the per-tier float assertions.

    Those pinned that `fast` was priced from `llm_price_haiku_*` and `reasoning`
    from `llm_price_opus_*` — Settings floats that were the published prices of
    RETIRED models. There is no tier map any more: whatever price the ledger
    resolves for the concrete model is the price applied, so a deliberately
    unusual snapshot must show up in the answer rather than a familiar default.
    """
    ledger = _fake_cost_ledger(input_micros=7_000_000, output_micros=11_000_000)
    adapter = _make_adapter(cost_ledger=ledger)
    mock_resp = _mock_anthropic_response(input_tokens=1_000_000, output_tokens=1_000_000)
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        await adapter.generate(_request(model="fast"))

    # The gate resolved a price for the CONCRETE model id, once, before egress.
    ledger.resolve_price_for.assert_awaited_once()
    assert ledger.resolve_price_for.await_args.kwargs["model"] == "claude-haiku-4-5-20251001"
    # ...and the ledger row was written from that same snapshot.
    snapshot = ledger.record_cost.await_args.kwargs["price"]
    assert snapshot.input_price_micros_per_mtok == 7_000_000
    assert snapshot.output_price_micros_per_mtok == 11_000_000


async def test_no_cost_ledger_refuses_before_egress() -> None:
    """THE FIX. With no ledger there is no price source, so the call is refused
    rather than priced from a Settings float — and refused BEFORE the provider is
    called, so nothing is billed for a call that fails."""
    adapter = _make_adapter(cost_ledger=None)
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        with pytest.raises(LLMModelNotPriced, match="no cost ledger"):
            await adapter.generate(_request())
        mock_cls.return_value.messages.create.assert_not_called()


async def test_a_pricing_gap_in_the_ledger_also_refuses_before_egress() -> None:
    """The other half of the same rule: a ledger that has no row for this model
    is no better than no ledger."""
    from skylize.dal.cost_ledger import PricingNotFound

    ledger = _fake_cost_ledger()
    ledger.resolve_price_for = AsyncMock(side_effect=PricingNotFound("no row"))
    adapter = _make_adapter(cost_ledger=ledger)
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        with pytest.raises(LLMModelNotPriced):
            await adapter.generate(_request())
        mock_cls.return_value.messages.create.assert_not_called()


def test_no_settings_price_fields_remain() -> None:
    """Guards the reintroduction. Two of the six removed floats were the prices
    of models that no longer exist; a second price source is how the two
    disagreed in the first place."""
    from skylize.config import Settings as _S

    assert [f for f in _S.model_fields if f.startswith("llm_price")] == []


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


# ---------------------------------------------------------------------------
# D1/D3 — SDK internal retry disabled; Settings-driven timeout. Asserted on
# BOTH construction sites so the shared _client_kwargs helper cannot drift
# between the two egresses.
# ---------------------------------------------------------------------------


async def test_sdk_internal_retry_disabled_and_timeout_set_on_sync_client() -> None:
    adapter = _make_adapter()
    req = _request()
    mock_resp = _mock_anthropic_response()
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_resp
        await adapter.generate(req)
    # The adapter is the sole retry authority (D1): the SDK must not retry.
    assert mock_cls.call_args.kwargs["max_retries"] == 0
    # Settings default (D3), not the SDK's ~600s default.
    assert mock_cls.call_args.kwargs["timeout"] == 120.0


async def test_sdk_internal_retry_disabled_and_timeout_set_on_async_client() -> None:
    adapter = _make_adapter(llm_timeout_seconds=7.5)
    req = _tools_request()
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value.messages.create = AsyncMock(return_value=_mock_tools_response())
        await adapter.generate_with_tools(req, tools=[])
    assert mock_cls.call_args.kwargs["max_retries"] == 0
    assert mock_cls.call_args.kwargs["timeout"] == 7.5  # the override reaches the client


# ---------------------------------------------------------------------------
# P3 — ONE SDK client per adapter, built on first use and reused; released by
# aclose(), never left to GC. Both egresses used to build a fresh client inside
# the request path and never close it — in the tool loop, one leaked
# AsyncAnthropic (and its TCP pool) per ITERATION.
# ---------------------------------------------------------------------------


async def test_sync_client_built_once_and_reused_across_calls() -> None:
    adapter = _make_adapter()
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = _mock_anthropic_response()
        await adapter.generate(_request())
        await adapter.generate(_request())
        await adapter.generate(_request())

    assert mock_cls.call_count == 1, (
        f"expected ONE sync client for three calls, got {mock_cls.call_count}"
    )
    # Three real provider calls did go out — the reuse is not swallowing calls.
    assert mock_cls.return_value.messages.create.call_count == 3
    # And the cached instance is the one the SDK handed back.
    assert adapter._sync_client_instance is mock_cls.return_value


async def test_async_client_built_once_across_tool_loop_iterations() -> None:
    """The leak this fixes: N tool-loop iterations = N calls to
    generate_with_tools, which used to be N AsyncAnthropic clients."""
    adapter = _make_adapter()
    with patch(
        "skylize.adapters.llm.anthropic_adapter.anthropic.AsyncAnthropic"
    ) as mock_cls:
        mock_cls.return_value.messages.create = AsyncMock(
            return_value=_mock_tools_response()
        )
        for _ in range(4):
            await adapter.generate_with_tools(_tools_request(), tools=[])

    assert mock_cls.call_count == 1, (
        f"expected ONE async client for four iterations, got {mock_cls.call_count}"
    )
    assert mock_cls.return_value.messages.create.await_count == 4
    assert adapter._async_client_instance is mock_cls.return_value


async def test_the_two_egresses_hold_separate_clients() -> None:
    """One sync + one async per adapter — the sync egress must not be handed the
    async client (its `messages.create` is awaited in a worker thread)."""
    adapter = _make_adapter()
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as sync_cls:
        with patch(
            "skylize.adapters.llm.anthropic_adapter.anthropic.AsyncAnthropic"
        ) as async_cls:
            sync_cls.return_value.messages.create.return_value = _mock_anthropic_response()
            async_cls.return_value.messages.create = AsyncMock(
                return_value=_mock_tools_response()
            )
            await adapter.generate(_request())
            await adapter.generate_with_tools(_tools_request(), tools=[])

    assert sync_cls.call_count == 1
    assert async_cls.call_count == 1
    assert adapter._sync_client_instance is not adapter._async_client_instance


async def test_aclose_closes_both_clients() -> None:
    adapter = _make_adapter()
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as sync_cls:
        with patch(
            "skylize.adapters.llm.anthropic_adapter.anthropic.AsyncAnthropic"
        ) as async_cls:
            sync_cls.return_value.messages.create.return_value = _mock_anthropic_response()
            async_cls.return_value.messages.create = AsyncMock(
                return_value=_mock_tools_response()
            )
            async_cls.return_value.close = AsyncMock()
            await adapter.generate(_request())
            await adapter.generate_with_tools(_tools_request(), tools=[])
            await adapter.aclose()

    sync_cls.return_value.close.assert_called_once()
    async_cls.return_value.close.assert_awaited_once()


async def test_aclose_is_a_noop_when_no_call_was_ever_made() -> None:
    """A container that never calls the provider opened no pool to close."""
    adapter = _make_adapter()
    assert adapter._sync_client_instance is None
    assert adapter._async_client_instance is None
    await adapter.aclose()  # must not raise


# ---------------------------------------------------------------------------
# D5 — an attempt budget below 1 is refused at boot, and _call_with_retry's
# fallthrough is a REAL raise (not an assert, which `python -O` compiles out)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1])
def test_settings_rejects_attempt_budget_below_one(bad: int) -> None:
    """Setting 0 to 'disable retries' is a plausible operator action; it would
    make `range(1, 1)` empty, so no provider request would ever be sent."""
    with pytest.raises(ValidationError) as ei:
        _settings(llm_retry_max_attempts=bad)
    message = str(ei.value)
    assert "SKYLIZE_LLM_RETRY_MAX_ATTEMPTS must be >= 1" in message
    # The error must explain that 1 means a single attempt with no retries.
    assert "Use 1 to disable retries: one attempt, no retry." in message


def test_settings_accepts_one_attempt() -> None:
    """1 is the legitimate 'no retries' value and must remain constructible."""
    assert _settings(llm_retry_max_attempts=1).llm_retry_max_attempts == 1


async def test_zero_attempt_budget_raises_typed_error_not_attributeerror() -> None:
    """The fallthrough must not depend on assertions being enabled.

    Settings now refuses < 1, so this drives the adapter with a plain settings
    stub that bypasses that validation — the same state `python -O` would reach
    with the old `assert last_exc is not None` compiled out, where the next line
    raised AttributeError on None.
    """
    stub = SimpleNamespace(
        anthropic_api_key="sk-test",
        anthropic_base_url="",
        llm_model_default="claude-sonnet-4-6",
        llm_model_fast="claude-haiku-4-5-20251001",
        llm_model_reasoning="claude-opus-4-6",
        llm_retry_max_attempts=0,  # not reachable through Settings any more
    )
    adapter = AnthropicAdapter(settings=stub)
    invoke = AsyncMock()

    with pytest.raises(LLMProviderUnavailable) as ei:
        await adapter._call_with_retry(invoke)

    invoke.assert_not_awaited()  # no provider request was attempted
    assert "llm_retry_max_attempts=0 is below 1" in str(ei.value)
    assert "TOTAL attempt count" in str(ei.value)
