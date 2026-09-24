"""build_stripe_refund_tool's handler, exercised directly (no ToolProxy).

Fills the gap test_stripe_gate.py and test_stripe_actions.py leave: the
former proves the GATE denies correctly, the latter proves the EXECUTOR
raises ChargeCurrencyMismatch correctly, but neither proves the HANDLER
translates that executor exception into the tool-facing
`ToolSpendDeferredToHuman` design 7.0 gap 1's resolution promises.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
import pytest
from pydantic import BaseModel

from skylize.app.stripe.actions import StripeRefundExecutor
from skylize.dal.stripe_accounts import InMemoryStripeAccountRepository, StripeAccountRow
from skylize.tools.base import ToolContext, ToolSpendDeferredToHuman
from skylize.tools.builtin.stripe_tools import (
    STRIPE_CREATE_REFUND_TOOL_ID,
    STRIPE_REFUND_CURRENCY,
    CreateRefundInput,
    build_stripe_refund_tool,
)

ORG = "org_test"
CHARGE_ID = "ch_fixture"


class _Recorder:
    def __init__(self, *, charge_currency: str) -> None:
        self._charge_currency = charge_currency
        self.post_calls = 0

    def __call__(self, **_kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url: str, **kw):
        return httpx.Response(200, json={"id": CHARGE_ID, "currency": self._charge_currency})

    async def post(self, url: str, **kw):
        self.post_calls += 1
        return httpx.Response(
            200, json={"id": "re_fixture", "status": "succeeded",
                       "amount": 500, "currency": self._charge_currency},
        )


async def _seeded_repo() -> InMemoryStripeAccountRepository:
    repo = InMemoryStripeAccountRepository()
    await repo.insert(StripeAccountRow(
        id=uuid.uuid4(), org_id=ORG, stripe_account_id="acct_fixture",
        livemode=False, scope="read_write",
        connected_at=datetime.now(timezone.utc), deauthorized_at=None,
    ))
    return repo


def _tool_and_handler(recorder: _Recorder, repo: InMemoryStripeAccountRepository):
    def executor_factory() -> StripeRefundExecutor:
        return StripeRefundExecutor(
            platform_secret_key="sk_test_fixture", http_client_factory=recorder,
        )

    tools = build_stripe_refund_tool(
        stripe_repo=repo, executor_factory=executor_factory, stripe_livemode=False,
    )
    assert len(tools) == 1
    assert tools[0].tool_id == STRIPE_CREATE_REFUND_TOOL_ID
    return tools[0]


def _ctx(hitl_id: uuid.UUID | None) -> ToolContext:
    return ToolContext(
        org_id=ORG, agent_id="test_agent", correlation_id=uuid.uuid4(), hitl_id=hitl_id,
    )


@pytest.mark.asyncio
async def test_handler_succeeds_on_a_matching_currency() -> None:
    repo = await _seeded_repo()
    recorder = _Recorder(charge_currency=STRIPE_REFUND_CURRENCY)
    tool = _tool_and_handler(recorder, repo)

    output = await tool.handler(
        CreateRefundInput(charge_id=CHARGE_ID, amount_minor=500, reason="test"),
        _ctx(uuid.uuid4()),
    )
    assert isinstance(output, BaseModel)
    assert output.refund_id == "re_fixture"  # type: ignore[attr-defined]
    assert recorder.post_calls == 1


@pytest.mark.asyncio
async def test_handler_defers_to_human_on_a_currency_mismatch() -> None:
    """Design 7.0 gap 1's promise: a mismatch surfaces as ToolSpendDeferredToHuman,
    the SAME disposition the [INTERIM-RULE] threshold check uses - not a hard
    denial, not a silent proceed, not an attempted conversion."""
    repo = await _seeded_repo()
    recorder = _Recorder(charge_currency="eur")  # tool is registered for "usd"
    tool = _tool_and_handler(recorder, repo)

    with pytest.raises(ToolSpendDeferredToHuman):
        await tool.handler(
            CreateRefundInput(charge_id=CHARGE_ID, amount_minor=500, reason="test"),
            _ctx(uuid.uuid4()),
        )
    assert recorder.post_calls == 0, "no money may move on a currency mismatch"


@pytest.mark.asyncio
async def test_handler_fails_execution_without_a_hitl_id() -> None:
    from skylize.tools.base import ToolExecutionError

    repo = await _seeded_repo()
    recorder = _Recorder(charge_currency=STRIPE_REFUND_CURRENCY)
    tool = _tool_and_handler(recorder, repo)

    with pytest.raises(ToolExecutionError):
        await tool.handler(
            CreateRefundInput(charge_id=CHARGE_ID, amount_minor=500, reason="test"),
            _ctx(None),
        )
