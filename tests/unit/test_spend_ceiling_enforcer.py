"""Unit tests for SpendCeilingEnforcer and its wiring into AnthropicAdapter.

Fakes for the ceiling DAL and cost ledger (no real DB): they pin the refusal
logic, the audit-record-plus-governance-event emission, and — through a patched
Anthropic SDK — that BOTH egresses refuse a breaching call BEFORE any SDK client
is constructed. The real-Postgres proofs live in
tests/integration/test_org_spend_ceiling_pg.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from skylize.adapters.llm.anthropic_adapter import AnthropicAdapter
from skylize.adapters.llm.gateway import (
    LLMContentBlock,
    LLMGenerateRequest,
    LLMGenerateWithToolsRequest,
    LLMMessage,
)
from skylize.adapters.llm.spend_ceiling import (
    OrgSpendCeilingExceeded,
    SpendCeilingEnforcer,
)
from skylize.app.audit.service import AuditService
from skylize.config import Settings
from skylize.dal.cost_ledger import PriceSnapshot
from skylize.dal.memory import InMemoryAuditRepository
from skylize.events.memory_bus import InMemoryEventBus

ORG = "org_ceiling_unit"
PERIOD = "2026-07"
_FIXED_NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)  # -> "2026-07"

# 1 micro-USD / token in and out, so a call's estimate/cost read in whole tokens.
_PRICE = PriceSnapshot(
    input_price_micros_per_mtok=1_000_000,
    output_price_micros_per_mtok=1_000_000,
    pricing_version=1,
    currency="USD",
)


class _FakeCeilingDAL:
    def __init__(self, ceiling: int | None) -> None:
        self._ceiling = ceiling
        self.reads: list[tuple[str, str]] = []

    async def read_ceiling_micros(self, org_id: str, billing_period: str) -> int | None:
        self.reads.append((org_id, billing_period))
        return self._ceiling


class _FakeCostLedger:
    """Doubles for both the adapter's price resolution and the enforcer's
    org-wide aggregate. record_cost must NEVER be called on a refused path."""

    def __init__(self, *, period_to_date: int, price: PriceSnapshot = _PRICE) -> None:
        self._period_to_date = period_to_date
        self._price = price
        self.record_cost_calls = 0
        # Every org-wide aggregate this fake was asked for. The aggregate is the
        # expensive read of the pre-egress pair (migration 0016), so a test can
        # assert it is not issued when it cannot change the outcome.
        self.aggregate_reads: list[tuple[str, str]] = []

    async def resolve_price_for(self, **_kwargs: object) -> PriceSnapshot:
        return self._price

    async def org_period_total_micros(self, org_id: str, billing_period: str) -> int:
        self.aggregate_reads.append((org_id, billing_period))
        return self._period_to_date

    async def record_cost(self, *_args: object, **_kwargs: object) -> object:
        self.record_cost_calls += 1
        raise AssertionError("record_cost must not run on a refused call")


def _enforcer(*, ceiling: int | None, period_to_date: int) -> tuple[
    SpendCeilingEnforcer, InMemoryEventBus, InMemoryAuditRepository, _FakeCostLedger
]:
    bus = InMemoryEventBus()
    repo = InMemoryAuditRepository()
    ledger = _FakeCostLedger(period_to_date=period_to_date)
    enforcer = SpendCeilingEnforcer(
        ceiling_dal=_FakeCeilingDAL(ceiling),
        cost_ledger=ledger,  # type: ignore[arg-type]
        audit=AuditService(bus, repo),
        bus=bus,
        now=lambda: _FIXED_NOW,
    )
    return enforcer, bus, repo, ledger


async def _enforce(enforcer: SpendCeilingEnforcer, *, attempted_tool: str = "llm.generate",
                   requested_max_tokens: int = 1_000, input_chars: int = 12) -> None:
    await enforcer.enforce(
        org_id=ORG,
        agent_id="agent_x",
        governance_token_id=uuid4(),
        correlation_id=uuid4(),
        attempted_tool=attempted_tool,
        input_chars=input_chars,
        requested_max_tokens=requested_max_tokens,
        price=_PRICE,
    )


# ---------------------------------------------------------------------------
# Enforcer logic
# ---------------------------------------------------------------------------


async def test_under_ceiling_allows_and_emits_nothing() -> None:
    enforcer, bus, repo, _ = _enforcer(ceiling=10_000_000, period_to_date=0)
    await _enforce(enforcer)  # does not raise
    assert bus.published_of_type("governance.scope_violation") == []
    assert repo.rows == []


async def test_breach_refuses_with_typed_error_fields() -> None:
    # period_to_date huge so any positive estimate breaches.
    enforcer, _, _, _ = _enforcer(ceiling=1, period_to_date=1_000_000)
    with pytest.raises(OrgSpendCeilingExceeded) as ei:
        await _enforce(enforcer, requested_max_tokens=1_000)
    err = ei.value
    assert err.org_id == ORG
    assert err.billing_period == PERIOD
    assert err.ceiling_micros == 1
    assert err.period_to_date_micros == 1_000_000
    assert err.estimated_micros >= 1_000  # >= output tokens at 1 micro/token
    assert err.period_to_date_micros + err.estimated_micros > err.ceiling_micros


async def test_breach_emits_audit_record_and_governance_event() -> None:
    enforcer, bus, repo, _ = _enforcer(ceiling=1, period_to_date=1_000_000)
    with pytest.raises(OrgSpendCeilingExceeded):
        await _enforce(enforcer, attempted_tool="llm.generate")

    # Governance event on the bus (existing type; failed_stage == "budget").
    violations = bus.published_of_type("governance.scope_violation")
    assert len(violations) == 1
    payload = violations[0].payload
    assert payload.failed_stage == "budget"
    assert payload.attempted_tool == "llm.generate"

    # Audit record: both the bus event AND the append-only row.
    assert len(bus.published_of_type("audit.action_recorded")) == 1
    assert len(repo.rows) == 1
    row = repo.rows[0]
    assert row.action_type == "governance.spend_ceiling_exceeded"
    assert row.result == "denied"


async def test_missing_ceiling_row_fails_closed() -> None:
    """D6: no ceiling row for (org, period) => refuse, ceiling_micros is None."""
    enforcer, bus, repo, _ = _enforcer(ceiling=None, period_to_date=0)
    with pytest.raises(OrgSpendCeilingExceeded) as ei:
        await _enforce(enforcer)
    assert ei.value.ceiling_micros is None
    assert "failing closed" in ei.value.reason
    assert len(bus.published_of_type("governance.scope_violation")) == 1
    assert len(repo.rows) == 1


# ---------------------------------------------------------------------------
# Read economy — the org-wide aggregate is the expensive half of the pre-egress
# pair (migration 0016). It must not be issued when it cannot change the outcome.
# ---------------------------------------------------------------------------


async def test_no_ceiling_refuses_without_reading_the_ledger_aggregate() -> None:
    """The aggregate used to run unconditionally, BEFORE the None check, on a
    path whose outcome is 'refuse' regardless — and that is the path a
    misconfigured org takes on EVERY call."""
    enforcer, _bus, _repo, ledger = _enforcer(ceiling=None, period_to_date=999)
    with pytest.raises(OrgSpendCeilingExceeded):
        await _enforce(enforcer)
    assert ledger.aggregate_reads == [], (
        "ai_cost_ledger was queried on the no-ceiling path, where the answer "
        "cannot affect the refusal"
    )


async def test_no_ceiling_reports_not_read_rather_than_a_zero_nobody_measured() -> None:
    """period_to_date_micros is None (= not read), not 0 (= measured as empty),
    and the reason still names the org, period, estimate and the D6 rule."""
    enforcer, _bus, _repo, _ledger = _enforcer(ceiling=None, period_to_date=999)
    with pytest.raises(OrgSpendCeilingExceeded) as ei:
        await _enforce(enforcer, requested_max_tokens=1_000, input_chars=0)
    assert ei.value.period_to_date_micros is None
    assert ei.value.estimated_micros == 1_000
    reason = ei.value.reason
    assert ORG in reason
    assert "2026-07" in reason  # the pinned billing period
    assert "failing closed (D6)" in reason
    assert "1000 micro-USD" in reason
    assert "period-to-date spend was not read" in reason


async def test_ceiling_present_still_reads_the_aggregate_exactly_once() -> None:
    """When a ceiling exists the aggregate IS needed — and one read, not two."""
    enforcer, _bus, _repo, ledger = _enforcer(ceiling=10_000_000, period_to_date=0)
    await _enforce(enforcer)
    assert ledger.aggregate_reads == [(ORG, "2026-07")]


async def test_projected_equal_to_ceiling_is_allowed() -> None:
    """The gate refuses only on strict breach (>). Projected == ceiling passes."""
    # estimate for input_chars=0, max_tokens=1000 at 1 micro/token = 1000 micros.
    enforcer, bus, _, _ = _enforcer(ceiling=1_000, period_to_date=0)
    await enforcer.enforce(
        org_id=ORG, agent_id="a", governance_token_id=uuid4(), correlation_id=uuid4(),
        attempted_tool="llm.generate", input_chars=0, requested_max_tokens=1_000, price=_PRICE,
    )  # 0 + 1000 == 1000, not > 1000 => allowed
    assert bus.published_of_type("governance.scope_violation") == []


# ---------------------------------------------------------------------------
# Adapter wiring — BOTH egresses refuse BEFORE the SDK is constructed
# ---------------------------------------------------------------------------


def _settings() -> Settings:
    return Settings(  # type: ignore[arg-type]
        anthropic_api_key="sk-test",
        llm_model_default="claude-sonnet-4-6",
        llm_model_fast="claude-haiku-4-5-20251001",
        llm_model_reasoning="claude-opus-4-6",
    )


def _adapter(*, ceiling: int | None, period_to_date: int) -> tuple[
    AnthropicAdapter, InMemoryEventBus, InMemoryAuditRepository, _FakeCostLedger
]:
    enforcer, bus, repo, ledger = _enforcer(ceiling=ceiling, period_to_date=period_to_date)
    adapter = AnthropicAdapter(
        settings=_settings(),
        cost_ledger=ledger,  # type: ignore[arg-type]
        spend_ceiling=enforcer,
    )
    return adapter, bus, repo, ledger


def _gen_request(**kw: object) -> LLMGenerateRequest:
    d: dict[str, object] = {
        "prompt": "Hello there, this is a test prompt",
        "requested_max_tokens": 1_000,
        "governance_token_id": uuid4(),
        "org_id": ORG,
        "correlation_id": uuid4(),
        "agent_id": "agent_x",
    }
    d.update(kw)
    return LLMGenerateRequest(**d)  # type: ignore[arg-type]


def _tools_request(**kw: object) -> LLMGenerateWithToolsRequest:
    d: dict[str, object] = {
        "messages": [LLMMessage(role="user", content=[LLMContentBlock(kind="text", text="hi")])],
        "requested_max_tokens": 1_000,
        "governance_token_id": uuid4(),
        "org_id": ORG,
        "correlation_id": uuid4(),
        "agent_id": "agent_x",
    }
    d.update(kw)
    return LLMGenerateWithToolsRequest(**d)  # type: ignore[arg-type]


async def test_generate_refuses_before_sdk_constructed() -> None:
    adapter, bus, repo, ledger = _adapter(ceiling=1, period_to_date=1_000_000)
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        with pytest.raises(OrgSpendCeilingExceeded):
            await adapter.generate(_gen_request())
        mock_cls.assert_not_called()  # SDK never touched
    assert ledger.record_cost_calls == 0  # nothing recorded
    assert len(bus.published_of_type("governance.scope_violation")) == 1
    assert len(repo.rows) == 1


async def test_generate_with_tools_refuses_before_sdk_constructed() -> None:
    adapter, bus, repo, ledger = _adapter(ceiling=1, period_to_date=1_000_000)
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.AsyncAnthropic") as mock_cls:
        with pytest.raises(OrgSpendCeilingExceeded):
            await adapter.generate_with_tools(_tools_request(), tools=[])
        mock_cls.assert_not_called()  # second egress: SDK never touched
    assert ledger.record_cost_calls == 0
    assert len(bus.published_of_type("governance.scope_violation")) == 1


async def test_unwired_ceiling_does_not_gate() -> None:
    """spend_ceiling=None (memory backend) => no gate; the call proceeds to the
    SDK exactly as before this feature."""
    ledger = _FakeCostLedger(period_to_date=1_000_000)
    adapter = AnthropicAdapter(
        settings=_settings(), cost_ledger=ledger, spend_ceiling=None,  # type: ignore[arg-type]
    )
    resp = MagicMock()
    resp.content = [MagicMock(type="text", text="ok")]
    resp.usage.input_tokens = 3
    resp.usage.output_tokens = 4
    resp.model = "claude-sonnet-4-6"
    resp.id = "msg_x"
    # record_cost would run post-call; give the fake a benign return for this path.
    ledger.record_cost = AsyncMock(return_value=MagicMock(cost_micros=7))  # type: ignore[method-assign]
    with patch("skylize.adapters.llm.anthropic_adapter.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = resp
        result = await adapter.generate(_gen_request())
    assert result.text == "ok"
    mock_cls.assert_called_once()  # SDK WAS invoked — no gate
