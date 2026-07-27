"""Cost-ledger wiring at the AnthropicAdapter egress (ADR-0006, Stage 2).

Covers, with a fake DAL (the real DAL + Postgres is proven in
tests/integration/test_cost_ledger_pg.py):
  * pre-call pricing gate: an unpriced concrete model refuses the call with a
    typed error BEFORE the SDK client is ever constructed (owner decision D1);
  * exactly one ledger row per completed call, on BOTH egresses;
  * the row records the provider's RESOLVED model id from the response, not
    the requested alias (owner decision D3);
  * failure paths (retry-exhausted 5xx, 429-exhausted, 401) write no row;
  * a ledger write failure fails LOUD (logged at ERROR with the
    correlation_id and surfaced) — never a silent success;
  * cost asserted to the cent for three seeded models, priced from the SAME
    integers migration 0013 seeds (imported, not duplicated).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import anthropic
import httpx
import pytest

from skylize.adapters.llm.anthropic_adapter import AnthropicAdapter
from skylize.adapters.llm.gateway import (
    LLMAuthenticationError,
    LLMContentBlock,
    LLMGenerateRequest,
    LLMGenerateWithToolsRequest,
    LLMMessage,
    LLMModelNotPriced,
    LLMProviderUnavailable,
    LLMRateLimited,
)
from skylize.config import Settings
from skylize.dal.cost_ledger import (
    CostObservation,
    CostRecord,
    PricingNotFound,
    PriceSnapshot,
    compute_cost_micros,
    micros_to_minor,
)

ORG = "org_test"

# The seeded price integers — imported from the migration so the fixture can
# never drift from what actually lands in model_pricing.
from importlib import util as _importlib_util
from pathlib import Path as _Path

_MIGRATION = (
    _Path(__file__).resolve().parents[2]
    / "migrations" / "versions" / "0013_seed_model_pricing.py"
)
_spec = _importlib_util.spec_from_file_location("migration_0013", _MIGRATION)
assert _spec is not None and _spec.loader is not None
_mig = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(_mig)
SEED_PRICES: dict[str, tuple[int, int]] = {
    model: (in_p, out_p)
    for (model, in_p, out_p, version, _f, _t) in _mig.SEED_PRICES
    if version == 1
}


class FakeCostLedger:
    """In-memory stand-in for CostLedgerDAL, priced from the 0013 seed."""

    def __init__(self, *, priced: bool = True) -> None:
        self.priced = priced
        self.observations: list[CostObservation] = []
        self.record_error: Exception | None = None

    async def resolve_price_for(
        self, *, org_id: str, provider: str, model: str, occurred_at: datetime
    ) -> PriceSnapshot:
        if not self.priced or model not in SEED_PRICES:
            raise PricingNotFound(f"no price for {model!r}")
        in_p, out_p = SEED_PRICES[model]
        return PriceSnapshot(
            input_price_micros_per_mtok=in_p,
            output_price_micros_per_mtok=out_p,
            pricing_version=1,
            currency="USD",
        )

    async def record_cost(self, obs: CostObservation) -> CostRecord:
        if self.record_error is not None:
            raise self.record_error
        price = await self.resolve_price_for(
            org_id=obs.org_id, provider=obs.provider, model=obs.model,
            occurred_at=obs.occurred_at,
        )
        self.observations.append(obs)
        return CostRecord(
            entry_id=uuid4(),
            cost_micros=compute_cost_micros(
                input_tokens=obs.input_tokens,
                output_tokens=obs.output_tokens,
                input_price_micros_per_mtok=price.input_price_micros_per_mtok,
                output_price_micros_per_mtok=price.output_price_micros_per_mtok,
            ),
            currency=price.currency,
            inserted=True,
        )


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "anthropic_api_key": "sk-test",
        "llm_model_default": "claude-sonnet-4-6",
        "llm_model_fast": "claude-haiku-4-5-20251001",
        "llm_model_reasoning": "claude-opus-4-6",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _adapter(ledger: FakeCostLedger | None) -> AnthropicAdapter:
    return AnthropicAdapter(settings=_settings(), cost_ledger=ledger)  # type: ignore[arg-type]


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


def _provider_message(
    *,
    model: str = "claude-sonnet-4-6",
    message_id: str = "msg_test_001",
    input_tokens: int = 10,
    output_tokens: int = 20,
    stop_reason: str = "end_turn",
) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = "World"
    resp = MagicMock()
    resp.content = [block]
    resp.usage.input_tokens = input_tokens
    resp.usage.output_tokens = output_tokens
    resp.model = model
    resp.id = message_id
    resp.stop_reason = stop_reason
    return resp


def _httpx_response(status: int) -> httpx.Response:
    dummy = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx.Response(status, request=dummy)


def _status_error(status: int) -> anthropic.APIStatusError:
    cls: type[anthropic.APIStatusError] = anthropic.APIStatusError
    if status == 429:
        cls = anthropic.RateLimitError
    elif status == 401:
        cls = anthropic.AuthenticationError
    return cls(message=f"status {status}", response=_httpx_response(status), body={})


def _patch_sleep():
    return patch(
        "skylize.adapters.llm.anthropic_adapter.asyncio.sleep", new_callable=AsyncMock
    )


# ---------------------------------------------------------------------------
# D1 — pre-call pricing gate: refuse BEFORE the SDK is invoked
# ---------------------------------------------------------------------------


async def test_unpriced_model_refused_before_sdk_on_generate() -> None:
    adapter = _adapter(FakeCostLedger(priced=False))
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        with pytest.raises(LLMModelNotPriced):
            await adapter.generate(_request())
        mock_cls.assert_not_called()  # the SDK client was never even constructed


async def test_unpriced_model_refused_before_sdk_on_generate_with_tools() -> None:
    adapter = _adapter(FakeCostLedger(priced=False))
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.AsyncAnthropic") as mock_cls:
        with pytest.raises(LLMModelNotPriced):
            await adapter.generate_with_tools(_tools_request(), tools=[])
        mock_cls.assert_not_called()


async def test_priced_model_proceeds_with_ledger_wired() -> None:
    ledger = FakeCostLedger()
    adapter = _adapter(ledger)
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = _provider_message()
        result = await adapter.generate(_request())
    assert result.text == "World"


async def test_no_ledger_wired_skips_pricing_gate() -> None:
    """Memory backend / unit harnesses: no ledger, no gate — unchanged behavior."""
    adapter = _adapter(None)
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = _provider_message()
        result = await adapter.generate(_request())
    assert result.text == "World"


async def test_settings_float_fallback_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D2: the Settings floats are a DEMOTED fallback that warns on every use;
    with a ledger wired the fallback (and its warning) never fires."""
    with caplog.at_level(logging.WARNING, logger="skylize"):
        adapter = _adapter(None)
        with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _provider_message()
            await adapter.generate(_request())
    assert any("settings_price_fallback_used" in r.getMessage() for r in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="skylize"):
        adapter = _adapter(FakeCostLedger())
        with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _provider_message()
            await adapter.generate(_request())
    assert not any(
        "settings_price_fallback_used" in r.getMessage() for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Exactly one ledger row per completed call, on BOTH egresses
# ---------------------------------------------------------------------------


async def test_generate_records_exactly_one_row() -> None:
    ledger = FakeCostLedger()
    adapter = _adapter(ledger)
    req = _request()
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = _provider_message(
            input_tokens=123, output_tokens=456, message_id="msg_gen_1"
        )
        await adapter.generate(req)

    assert len(ledger.observations) == 1
    obs = ledger.observations[0]
    assert obs.org_id == ORG
    assert obs.correlation_id == req.correlation_id
    assert obs.agent_id == req.agent_id
    assert obs.run_id == req.governance_token_id
    assert obs.provider == "anthropic"
    assert obs.input_tokens == 123
    assert obs.output_tokens == 456
    assert obs.idempotency_key == "msg_gen_1"  # the provider response id
    assert obs.billing_period == obs.occurred_at.strftime("%Y-%m")


async def test_generate_with_tools_records_exactly_one_row() -> None:
    ledger = FakeCostLedger()
    adapter = _adapter(ledger)
    req = _tools_request()
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value.messages.create = AsyncMock(
            return_value=_provider_message(
                input_tokens=50, output_tokens=60, message_id="msg_tools_1"
            )
        )
        await adapter.generate_with_tools(req, tools=[])

    assert len(ledger.observations) == 1
    obs = ledger.observations[0]
    assert obs.correlation_id == req.correlation_id
    assert obs.agent_id == req.agent_id
    assert obs.run_id == req.governance_token_id
    assert obs.input_tokens == 50
    assert obs.output_tokens == 60
    assert obs.idempotency_key == "msg_tools_1"


# ---------------------------------------------------------------------------
# D3 — the row records the RESOLVED model id from the response
# ---------------------------------------------------------------------------


async def test_row_records_resolved_model_id_not_requested_alias() -> None:
    """Requested logical "fast" maps to the dated haiku id; the provider reports
    serving the ALIAS form — the ledger must record what the provider reports."""
    ledger = FakeCostLedger()
    adapter = _adapter(ledger)
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = _provider_message(
            model="claude-haiku-4-5"  # resolved form differs from requested
        )
        await adapter.generate(_request(model="fast"))

    assert len(ledger.observations) == 1
    # Requested concrete id was claude-haiku-4-5-20251001; recorded is the
    # provider's first-hand resolved id.
    assert ledger.observations[0].model == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Failure paths write NO row (timeout / 429-exhausted / 401)
# ---------------------------------------------------------------------------


async def test_retry_exhausted_5xx_writes_no_row() -> None:
    ledger = FakeCostLedger()
    adapter = _adapter(ledger)
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.side_effect = [_status_error(500)] * 3
        with _patch_sleep():
            with pytest.raises(LLMProviderUnavailable):
                await adapter.generate(_request())
    assert ledger.observations == []


async def test_rate_limit_exhausted_writes_no_row() -> None:
    ledger = FakeCostLedger()
    adapter = _adapter(ledger)
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.side_effect = [_status_error(429)] * 3
        with _patch_sleep():
            with pytest.raises(LLMRateLimited):
                await adapter.generate(_request())
    assert ledger.observations == []


async def test_401_writes_no_row() -> None:
    ledger = FakeCostLedger()
    adapter = _adapter(ledger)
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.side_effect = [_status_error(401)]
        with _patch_sleep():
            with pytest.raises(LLMAuthenticationError):
                await adapter.generate(_request())
    assert ledger.observations == []


async def test_tools_retry_exhausted_writes_no_row() -> None:
    ledger = FakeCostLedger()
    adapter = _adapter(ledger)
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value.messages.create = AsyncMock(
            side_effect=[_status_error(500)] * 3
        )
        with _patch_sleep():
            with pytest.raises(LLMProviderUnavailable):
                await adapter.generate_with_tools(_tools_request(), tools=[])
    assert ledger.observations == []


# ---------------------------------------------------------------------------
# Ledger write failure fails LOUD — surfaced, logged at ERROR w/ correlation_id
# ---------------------------------------------------------------------------


async def test_ledger_write_failure_is_loud(caplog: pytest.LogCaptureFixture) -> None:
    ledger = FakeCostLedger()
    ledger.record_error = RuntimeError("pg down")
    adapter = _adapter(ledger)
    req = _request()
    with caplog.at_level(logging.ERROR, logger="skylize"):
        with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _provider_message()
            with pytest.raises(RuntimeError, match="pg down"):
                await adapter.generate(req)

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records, "ledger failure must be logged at ERROR"
    assert any(str(req.correlation_id) in r.getMessage() for r in error_records)


# ---------------------------------------------------------------------------
# Cost to the cent, for three seeded models (fixture = migration 0013 integers)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("logical", "concrete", "in_tok", "out_tok", "expected_cents"),
    [
        # sonnet 4-6: (2M * 3.00 + 1M * 15.00) / 1M = $21.00 -> 2100 cents
        ("default", "claude-sonnet-4-6", 2_000_000, 1_000_000, 2100),
        # haiku 4.5: (1M * 1.00 + 1M * 5.00) / 1M = $6.00 -> 600 cents
        ("fast", "claude-haiku-4-5-20251001", 1_000_000, 1_000_000, 600),
        # opus 4.6: (500k * 5.00 + 200k * 25.00) / 1M = $7.50 -> 750 cents
        ("reasoning", "claude-opus-4-6", 500_000, 200_000, 750),
    ],
)
async def test_cost_to_the_cent_per_seeded_model(
    logical: str, concrete: str, in_tok: int, out_tok: int, expected_cents: int
) -> None:
    ledger = FakeCostLedger()
    adapter = _adapter(ledger)
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = _provider_message(
            model=concrete, input_tokens=in_tok, output_tokens=out_tok,
            message_id=f"msg_{concrete}",
        )
        response = await adapter.generate(_request(model=logical))

    # The response's cost and the ledger row come from the SAME resolution.
    assert int(micros_to_minor(response.cost_usd_micros)) == expected_cents
    obs = ledger.observations[0]
    in_p, out_p = SEED_PRICES[concrete]
    assert response.cost_usd_micros == compute_cost_micros(
        input_tokens=obs.input_tokens,
        output_tokens=obs.output_tokens,
        input_price_micros_per_mtok=in_p,
        output_price_micros_per_mtok=out_p,
    )
