"""The one externally-mutating Stripe verb this design authorizes: a refund.

WHAT THIS DOES, AND WHAT IT DELIBERATELY DOES NOT
--------------------------------------------------
One call: `POST /v1/refunds` against the connected account, authenticated
with the PLATFORM secret key plus a `Stripe-Account: acct_...` header (design
3.0.3, 4.0.1) - never a per-tenant bearer token, because none is stored.

NOT a charge-creation verb, NOT a transfer verb, NOT a payout verb. Refunds
only. Every other Stripe write this design might eventually need is a
separate pass with its own gate review.

IDEMPOTENCY - THE SAME SHAPE AS THE GCP KILL SWITCH, DELIBERATELY
-------------------------------------------------------------------
`HitlQueueService.approve` mints a FRESH correlation id per approval attempt
(app/hitl/service.py:160) and releases the row to 'pending' on a transient
failure, so a human retry re-runs everything. An idempotency key derived from
`correlation_id` would therefore differ on every retry and defeat Stripe's own
deduplication - the exact mechanism that makes the retry safe.

The `Idempotency-Key` is therefore derived from `hitl_id`, per design 7.0.1:

    uuid5(SKYLIZE_NAMESPACE, f"{hitl_id}:{charge_id}:{amount_minor}")

Stable across every replay of one approved decision; distinct across
different decisions and different refund targets. This is the SAME
derivation the GCP kill-switch design uses for Google's `requestId`
(app/gcp/actions.py `request_id_for`), so the two externally-mutating
connectors in this repo share one idempotency discipline (design 7.0.1).

WHEN THERE IS NO `hitl_id` THIS ACTION REFUSES. It does not fall back to
`correlation_id`. Design 7.0.1's Q2.1c resolution ("refunds always defer to
a human") means `hitl_id` is ALWAYS present on this path in production; a
missing one here means the caller built this action wrong, not that a
weaker guarantee is acceptable.

Stripe additionally requires a LOCAL dedupe record beyond its own 24-hour
idempotency-key window (design 7.0.1) - that is `ToolProxy`'s replay-key
machinery (`Reservation.replay_key`, `result_snapshot`), not this module's
job. This module is the thin HTTP boundary; the durable replay safety lives
in the ledger, one layer up.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

log = logging.getLogger("skylize.stripe.actions")

STRIPE_API_BASE = "https://api.stripe.com/v1"

#: Namespace for deriving the Stripe Idempotency-Key from hitl_id, per design
#: 7.0.1. Its own namespace, distinct from app.gcp.actions's requestId
#: namespace and from tools.base's _REPLAY_KEY_NS, so a derived key from one
#: system can never coincide with a derived key from another.
_IDEMPOTENCY_KEY_NS = uuid5(NAMESPACE_URL, "skylize.stripe.refund_idempotency_key")

_TIMEOUT_SECONDS = 30.0


class StripeActionError(RuntimeError):
    """A Stripe refund action could not be completed."""


class MissingIdempotencyAnchor(StripeActionError):
    """No `hitl_id` was available, so no retry-stable Idempotency-Key can be
    derived.

    Deliberately fatal rather than falling back to `correlation_id`: that
    value is fresh on every approval attempt, so a fallback would produce a
    DIFFERENT idempotency key on the retry path Stripe's deduplication exists
    to protect, while still looking correct. Design 7.0.1's Q2.1c resolution
    means this should never actually happen in production - every refund
    defers to a human, so `hitl_id` is always present - but the refusal
    exists so a future non-refund reuse of this module cannot silently drop
    the guarantee.
    """


class DestinationChargeRefused(StripeActionError):
    """The target charge is not a direct charge Skylize is authorized to
    refund under design doc 2.0's hard constraint.

    This module never inspects the charge's type itself - that invariant is
    checked at the gate (`ToolStripeChargeTypeForbidden`, tools/proxy.py
    `_authorize_stripe`) before this executor is ever reached. This exception
    exists only so a caller that somehow bypasses the gate (a direct unit
    test, a future refactor) fails loudly rather than silently refunding
    through a header-less call that would hit the PLATFORM's own charges.
    """


class ChargeCurrencyMismatch(StripeActionError):
    """The charge's ACTUAL currency does not match the tool's declared,
    frozen `ToolSpendProfile.currency` (design 7.0 gap 1, resolved 2026-09-19).

    `ToolSpendProfile.currency` is frozen at tool registration
    (tools/base.py:156,163) - one Stripe refund tool is registered for one
    currency. A refund's real currency belongs to the CHARGE, which this
    single-currency tool cannot know until it reads it. Rather than silently
    proceeding (which would let a mismatched-currency refund pass an
    envelope ceiling denominated in the wrong money) or attempting a
    currency conversion (out of scope, and not this module's job), this
    checks the charge's currency BEFORE calling `/v1/refunds` and refuses -
    surfaced by the tool handler as a defer-to-human, the same disposition
    the [INTERIM-RULE] threshold check uses (design 7.5.4), not a silent
    proceed and not an attempted conversion.
    """

    def __init__(self, *, declared: str, actual: str, charge_id: str) -> None:
        self.declared = declared
        self.actual = actual
        self.charge_id = charge_id
        super().__init__(
            f"charge {charge_id!r} is denominated in {actual!r}, but this "
            f"refund tool is registered for {declared!r}; refusing rather "
            f"than silently refunding in the wrong currency or converting"
        )


def idempotency_key_for(hitl_id: UUID, charge_id: str, amount_minor: int) -> UUID:
    """A deterministic, retry-stable Idempotency-Key for one (approval, charge,
    amount) triple, per design 7.0.1.

    Stable across every replay of the SAME approved decision - which is what
    makes Stripe treat a retry as a duplicate rather than a new refund.
    Distinct across different decisions and different refund targets.
    """
    return uuid5(_IDEMPOTENCY_KEY_NS, f"{hitl_id}:{charge_id}:{amount_minor}")


@dataclass(frozen=True, slots=True)
class RefundOutcome:
    """What Stripe reported back for one refund attempt."""

    refund_id: str
    status: str
    amount_minor: int
    currency: str


class StripeRefundExecutor:
    """Performs the one `POST /v1/refunds` call this design authorizes.

    Holds no per-tenant secret - authentication is the PLATFORM secret key
    (constructor argument, resolved from the secrets manager by the
    composition root, exactly as `GcpKillSwitchExecutor` never sees a raw env
    var either) plus the `Stripe-Account` header naming the connected
    account (design 3.0.3).
    """

    def __init__(
        self,
        *,
        platform_secret_key: str,
        http_client_factory: Any,
        api_base: str = STRIPE_API_BASE,
    ) -> None:
        self._secret_key = platform_secret_key
        self._client_factory = http_client_factory
        self._api_base = api_base

    async def get_charge_currency(
        self, *, stripe_account_id: str, charge_id: str
    ) -> str:
        """Read-only lookup of the charge's actual currency.

        Needs only the platform secret key plus the Stripe-Account header -
        design 7.0's own table says reads need no additional authorization
        beyond that. Used by `create_refund` to check for a currency mismatch
        (design 7.0 gap 1) BEFORE any money moves.
        """
        async with self._client_factory() as client:
            response = await client.get(
                f"{self._api_base}/charges/{charge_id}",
                headers={
                    "Authorization": f"Bearer {self._secret_key}",
                    "Stripe-Account": stripe_account_id,
                },
                timeout=_TIMEOUT_SECONDS,
            )
        if response.status_code >= 400:
            raise StripeActionError(
                f"Stripe charge lookup failed ({response.status_code}): {response.text}"
            )
        body: dict[str, Any] = response.json()
        currency: str = body["currency"]
        return currency

    async def create_refund(
        self,
        *,
        stripe_account_id: str,
        charge_id: str,
        amount_minor: int,
        declared_currency: str,
        hitl_id: UUID | None,
    ) -> RefundOutcome:
        """Refund `amount_minor` of `charge_id` on the connected account.

        The direct-charge invariant (design 2.0) is enforced at the
        `ToolProxy` gate, BEFORE this method is ever called - this method
        trusts that check and does not re-derive it, the same division of
        labour `_stop_instance` uses with `_authorize_wif` (tools/builtin/
        gcp_tools.py:20 "the handler therefore does no authorization of its
        own").

        THE CURRENCY CHECK (design 7.0 gap 1, resolved 2026-09-19) runs
        BEFORE any money moves: `declared_currency` is this tool's frozen
        `ToolSpendProfile.currency`; the charge's ACTUAL currency is read via
        `get_charge_currency` and compared. A mismatch raises
        `ChargeCurrencyMismatch` rather than proceeding or converting - the
        tool handler surfaces this as a defer-to-human, never a silent
        refund in the wrong currency.
        """
        if hitl_id is None:
            raise MissingIdempotencyAnchor(
                "no hitl_id is available, so no retry-stable Idempotency-Key "
                "can be derived. Design 7.0.1's Q2.1c resolution means every "
                "refund defers to a human and hitl_id should always be "
                "present here; refusing rather than falling back to "
                "correlation_id, which is minted fresh per approval attempt."
            )

        actual_currency = await self.get_charge_currency(
            stripe_account_id=stripe_account_id, charge_id=charge_id
        )
        if actual_currency.lower() != declared_currency.lower():
            raise ChargeCurrencyMismatch(
                declared=declared_currency, actual=actual_currency, charge_id=charge_id
            )

        key = idempotency_key_for(hitl_id, charge_id, amount_minor)

        async with self._client_factory() as client:
            response = await client.post(
                f"{self._api_base}/refunds",
                headers={
                    "Authorization": f"Bearer {self._secret_key}",
                    "Stripe-Account": stripe_account_id,
                    "Idempotency-Key": str(key),
                },
                data={"charge": charge_id, "amount": amount_minor},
                timeout=_TIMEOUT_SECONDS,
            )

        if response.status_code >= 400:
            raise StripeActionError(
                f"Stripe refund failed ({response.status_code}): {response.text}"
            )

        body = response.json()
        return RefundOutcome(
            refund_id=body["id"],
            status=body["status"],
            amount_minor=body["amount"],
            currency=body["currency"],
        )
