"""StripeRefundExecutor: idempotency-key derivation and the currency check.

The single most important property in this file is
`test_idempotency_key_is_stable_across_retries_of_the_same_approval`. That is
the one that makes Stripe's deduplication actually protect the HITL retry
path (design 7.0.1), and it is the one a refactor toward `correlation_id`
would silently break.

`test_create_refund_refuses_on_a_currency_mismatch_before_calling_refunds`
proves design 7.0 gap 1's resolution: the currency check runs BEFORE
`/v1/refunds` is ever called, so a mismatch never moves money.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from skylize.app.stripe.actions import (
    ChargeCurrencyMismatch,
    MissingIdempotencyAnchor,
    StripeActionError,
    StripeRefundExecutor,
    idempotency_key_for,
)

SECRET_KEY = "sk_test_fake"
ACCOUNT_ID = "acct_fake123"
CHARGE_ID = "ch_fake456"


class _Recorder:
    """Records every request and replays scripted responses by method+path."""

    def __init__(self, *, charge_currency: str = "usd", refund=None) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self._charge_currency = charge_currency
        self._refund = refund if refund is not None else httpx.Response(
            200, json={"id": "re_fake", "status": "succeeded",
                       "amount": 500, "currency": "usd"},
        )

    def __call__(self, **_kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url: str, **kw):
        self.calls.append(("GET", url, dict(kw.get("headers") or {})))
        return httpx.Response(200, json={"id": CHARGE_ID, "currency": self._charge_currency})

    async def post(self, url: str, **kw):
        self.calls.append(("POST", url, dict(kw.get("headers") or {})))
        if isinstance(self._refund, Exception):
            raise self._refund
        return self._refund


def _executor(recorder: _Recorder) -> StripeRefundExecutor:
    return StripeRefundExecutor(
        platform_secret_key=SECRET_KEY, http_client_factory=recorder
    )


def test_idempotency_key_is_stable_across_retries_of_the_same_approval() -> None:
    hitl_id = uuid.uuid4()
    first = idempotency_key_for(hitl_id, CHARGE_ID, 500)
    second = idempotency_key_for(hitl_id, CHARGE_ID, 500)
    assert first == second


def test_idempotency_key_is_distinct_across_different_charges() -> None:
    hitl_id = uuid.uuid4()
    a = idempotency_key_for(hitl_id, "ch_a", 500)
    b = idempotency_key_for(hitl_id, "ch_b", 500)
    assert a != b


def test_idempotency_key_is_distinct_across_different_amounts() -> None:
    hitl_id = uuid.uuid4()
    a = idempotency_key_for(hitl_id, CHARGE_ID, 500)
    b = idempotency_key_for(hitl_id, CHARGE_ID, 600)
    assert a != b


def test_idempotency_key_is_distinct_across_different_approvals() -> None:
    a = idempotency_key_for(uuid.uuid4(), CHARGE_ID, 500)
    b = idempotency_key_for(uuid.uuid4(), CHARGE_ID, 500)
    assert a != b


@pytest.mark.asyncio
async def test_create_refund_refuses_without_a_hitl_id() -> None:
    executor = _executor(_Recorder())
    with pytest.raises(MissingIdempotencyAnchor):
        await executor.create_refund(
            stripe_account_id=ACCOUNT_ID, charge_id=CHARGE_ID,
            amount_minor=500, declared_currency="usd", hitl_id=None,
        )


@pytest.mark.asyncio
async def test_create_refund_succeeds_on_a_matching_currency() -> None:
    recorder = _Recorder(charge_currency="usd")
    executor = _executor(recorder)
    outcome = await executor.create_refund(
        stripe_account_id=ACCOUNT_ID, charge_id=CHARGE_ID,
        amount_minor=500, declared_currency="usd", hitl_id=uuid.uuid4(),
    )
    assert outcome.refund_id == "re_fake"
    assert outcome.amount_minor == 500
    # Both the currency-check GET and the refund POST happened, in that order.
    methods = [c[0] for c in recorder.calls]
    assert methods == ["GET", "POST"]


@pytest.mark.asyncio
async def test_create_refund_refuses_on_a_currency_mismatch_before_calling_refunds() -> None:
    """Design 7.0 gap 1, resolved 2026-09-19: the tool is registered for one
    currency; a charge denominated in another must defer, not process, and
    must not move any money getting there."""
    recorder = _Recorder(charge_currency="eur")
    executor = _executor(recorder)
    with pytest.raises(ChargeCurrencyMismatch) as exc_info:
        await executor.create_refund(
            stripe_account_id=ACCOUNT_ID, charge_id=CHARGE_ID,
            amount_minor=500, declared_currency="usd", hitl_id=uuid.uuid4(),
        )
    assert exc_info.value.declared == "usd"
    assert exc_info.value.actual == "eur"
    # ONLY the currency-check GET happened. /v1/refunds was never called -
    # no money moved.
    methods = [c[0] for c in recorder.calls]
    assert methods == ["GET"]


@pytest.mark.asyncio
async def test_create_refund_currency_check_is_case_insensitive() -> None:
    """Stripe's currency codes are lowercase in the API; guard against a
    case-sensitivity false-positive mismatch."""
    recorder = _Recorder(charge_currency="USD")
    executor = _executor(recorder)
    outcome = await executor.create_refund(
        stripe_account_id=ACCOUNT_ID, charge_id=CHARGE_ID,
        amount_minor=500, declared_currency="usd", hitl_id=uuid.uuid4(),
    )
    assert outcome.refund_id == "re_fake"


@pytest.mark.asyncio
async def test_create_refund_surfaces_a_stripe_error_response() -> None:
    recorder = _Recorder(refund=httpx.Response(402, text="card_declined"))
    executor = _executor(recorder)
    with pytest.raises(StripeActionError, match="402"):
        await executor.create_refund(
            stripe_account_id=ACCOUNT_ID, charge_id=CHARGE_ID,
            amount_minor=500, declared_currency="usd", hitl_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_create_refund_sends_the_idempotency_key_header() -> None:
    recorder = _Recorder()
    executor = _executor(recorder)
    hitl_id = uuid.uuid4()
    await executor.create_refund(
        stripe_account_id=ACCOUNT_ID, charge_id=CHARGE_ID,
        amount_minor=500, declared_currency="usd", hitl_id=hitl_id,
    )
    post_headers = next(h for m, _u, h in recorder.calls if m == "POST")
    expected_key = str(idempotency_key_for(hitl_id, CHARGE_ID, 500))
    assert post_headers["Idempotency-Key"] == expected_key
    assert post_headers["Stripe-Account"] == ACCOUNT_ID
