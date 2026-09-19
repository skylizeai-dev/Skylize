"""The one governed Stripe verb in the registry: refund a direct charge.

THE ONLY EXTERNALLY-MUTATING STRIPE VERB. No charge-creation verb, no
transfer verb, no payout verb - see `app/stripe/actions.py` for why refunds
only, this pass.

HOW IT IS GOVERNED, and why none of it lives in this handler
--------------------------------------------------------------
Every check that decides WHETHER this may run happens before the handler is
entered, at the `ToolProxy` gates (tools/proxy.py):

  * token validation      - signature, expiry, revocation, scope, live-state.
  * the `stripe` gate     - a connected account exists for the running mode,
    its GRANTED scope covers this tool's required scope, the direct-charge
    invariant holds (design 2.0), and - because this tool also declares
    `spend` - the [INTERIM-RULE] large-refund review trigger (design 7.5.4).
  * the `spend` gate      - reserves the ceiling hold; the replay guard ahead
    of it is what makes a HITL retry return the prior result instead of
    refunding twice (design 7.0.1).

Q2.1c (design 7.0.1) is RESOLVED: refunds always defer to a human. There is
no numeric refund ceiling and no path where this tool executes without
`ToolContext.hitl_id` present - see `StripeRefundExecutor.create_refund`,
which refuses outright without one. The handler therefore does no
authorization of its own; what it DOES require is `hitl_id`, for the same
reason `_stop_instance` does (tools/builtin/gcp_tools.py:20-24): it is the
only value on this path that survives a HITL retry, so it is the only honest
basis for the provider-side idempotency this action depends on.

SETTLEMENT. `actual_amount_field="actual_refunded_minor"` on the spend
profile - Stripe can refund LESS than requested (a charge that was already
partially refunded, a charge whose remaining refundable amount is smaller
than requested), and `ToolProxy` settles the ledger hold with whatever this
field reports rather than the full reservation (`_settlement_amount`,
tools/proxy.py:451,477-490 - the generic settlement mechanism landed by PR
#14, and separately authorized for THIS tool specifically to consume, per
the 2026-09-16 refund-tool-scoped authorization note in the design doc's
7.0.1 - not a general ratification of that mechanism for every future
money-moving tool).

NOT GRANTED TO ANY STATELESS AGENT in this pass. No contract in
`contracts/mvp/` is wired with this tool_id; registering the real handler
here does not by itself grant it to anything, exactly as `stripe.refund`
already existing as a bare scope string (design 7.0.1's registration note)
did not.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ...app.stripe.actions import (
    ChargeCurrencyMismatch,
    DestinationChargeRefused,
    MissingIdempotencyAnchor,
    StripeActionError,
    StripeRefundExecutor,
)
from ...dal.stripe_accounts import StripeAccountRepository
from ..base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionError,
    ToolSpendDeferredToHuman,
    ToolSpendProfile,
    ToolStripeNotConnected,
    ToolStripeProfile,
)

STRIPE_CREATE_REFUND_TOOL_ID = "stripe.refund"

#: This refund tool's single, frozen settlement currency (design 7.0 gap 1,
#: resolved 2026-09-19: a single-currency tool with a handler-level check,
#: not a dynamic per-call currency - `ToolSpendProfile.currency` cannot
#: express the latter, and `org_stripe_accounts` (design 4.0.2) deliberately
#: carries no currency field to source one from; it is identity/authority
#: only, not a mirror of Stripe account capabilities). A merchant taking
#: charges in another currency needs a SEPARATE registered tool instance for
#: that currency - out of scope for this pass.
STRIPE_REFUND_CURRENCY = "usd"


class CreateRefundInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    charge_id: str = Field(min_length=1, description="Stripe charge id to refund (ch_...)")
    amount_minor: int = Field(
        gt=0,
        description=(
            "Amount to refund, in the charge currency's MINOR units (cents). "
            "Full refunds must be resolved to an explicit amount before this "
            "tool is called (design 7.0 gap 2) - this tool does not accept an "
            "omitted amount."
        ),
    )
    reason: str = Field(
        min_length=1, max_length=2000,
        description="Why this refund is being issued. Recorded in the audit trail.",
    )
    # Destination-charge fields, deliberately accepted on the INPUT SCHEMA so
    # the gate (ToolStripeChargeTypeForbidden) can see and refuse them off the
    # VALIDATED input, per design 2.0 and ToolStripeProfile.mutates_money.
    # None on every direct-charge call this design authorizes.
    on_behalf_of: str | None = Field(
        default=None,
        description="MUST be omitted. Presence converts this into a forbidden charge type.",
    )
    transfer_data: dict[str, Any] | None = Field(
        default=None,
        description="MUST be omitted. Presence converts this into a forbidden charge type.",
    )
    application_fee_amount: int | None = Field(
        default=None,
        description="MUST be omitted. Presence converts this into a forbidden charge type.",
    )


class CreateRefundOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refund_id: str
    status: str
    #: What Stripe actually refunded, in minor units. Read by ToolProxy as
    #: `actual_amount_field` to settle the ledger hold - see module docstring.
    actual_refunded_minor: int
    currency: str


def build_stripe_refund_tool(
    *,
    stripe_repo: StripeAccountRepository | None,
    executor_factory: Any,
    stripe_livemode: bool,
) -> list[ToolDefinition]:
    """Return the Stripe refund tool, or EMPTY when the feature is not wired.

    Returning nothing when `stripe_repo` or the executor is absent keeps every
    existing deployment byte-identical: the tool is not registered, so it
    cannot be resolved, granted, or invoked. Same shape as `build_gcp_tools`
    (tools/builtin/gcp_tools.py:91-104).
    """
    if stripe_repo is None or executor_factory is None:
        return []

    async def _create_refund(
        data: BaseModel, ctx: ToolContext
    ) -> CreateRefundOutput:
        assert isinstance(data, CreateRefundInput)

        # The gate already proved a live connection exists for this mode; it
        # is re-read here because the handler needs the row's
        # stripe_account_id, not to re-authorize (same division of labour as
        # `_stop_instance`, tools/builtin/gcp_tools.py:111-113).
        row = await stripe_repo.get_connected(ctx.org_id, livemode=stripe_livemode)
        if row is None:  # pragma: no cover - the gate refuses this first
            raise ToolStripeNotConnected(
                f"org {ctx.org_id!r} has no Stripe account connected for "
                f"{'live' if stripe_livemode else 'test'} mode"
            )

        executor: StripeRefundExecutor = executor_factory()
        try:
            outcome = await executor.create_refund(
                stripe_account_id=row.stripe_account_id,
                charge_id=data.charge_id,
                amount_minor=data.amount_minor,
                declared_currency=STRIPE_REFUND_CURRENCY,
                # NOT ctx.correlation_id. See ToolContext.hitl_id and
                # app/stripe/actions.py: correlation_id is minted fresh on
                # every approval attempt, so keying idempotency on it would
                # defeat the deduplication it exists to protect.
                hitl_id=ctx.hitl_id,
            )
        except ChargeCurrencyMismatch as exc:
            # Design 7.0 gap 1, resolved 2026-09-19: NOT a silent proceed and
            # NOT an attempted conversion. Surfaced through the SAME
            # defer-to-human disposition the [INTERIM-RULE] threshold check
            # uses (design 7.5.4) rather than a hard denial, because nothing
            # about the customer's authorization is wrong - the tool is
            # simply registered for a different currency than this charge.
            # No money has moved: this check runs BEFORE the refund call.
            raise ToolSpendDeferredToHuman(str(exc)) from exc
        except MissingIdempotencyAnchor as exc:
            # Surfaced as a tool execution failure rather than a denial:
            # nothing about the customer's authorization is wrong, the call
            # simply cannot be made safely outside an approved HITL replay.
            raise ToolExecutionError(str(exc)) from exc
        except DestinationChargeRefused as exc:  # pragma: no cover - gate refuses this first
            raise ToolExecutionError(str(exc)) from exc
        except StripeActionError as exc:
            raise ToolExecutionError(f"Stripe refund failed: {exc}") from exc

        return CreateRefundOutput(
            refund_id=outcome.refund_id,
            status=outcome.status,
            actual_refunded_minor=outcome.amount_minor,
            currency=outcome.currency,
        )

    return [
        ToolDefinition(
            tool_id=STRIPE_CREATE_REFUND_TOOL_ID,
            name="Refund a Stripe charge",
            description=(
                "Refund a specific amount of a specific Stripe charge on the "
                "connected account. Always defers to a human approval before "
                "executing (design 7.0.1, Q2.1c) - there is no autonomous "
                "refund path. Full refunds must be resolved to an explicit "
                "minor-unit amount by the caller before invoking this tool."
            ),
            input_schema=CreateRefundInput,
            output_schema=CreateRefundOutput,
            category="integration",
            handler=_create_refund,
            # The fifth proxy-enforced gate. Names the scope this tool
            # requires and turns on the direct-charge invariant check.
            stripe=ToolStripeProfile(
                required_scope="read_write",
                mutates_money=True,
            ),
            # Spend-capable: a refund moves money against the org's envelope
            # (design 7.0). `actual_amount_field` names the settlement field -
            # see module docstring for the 2026-09-16 refund-tool-scoped
            # authorization to consume that mechanism.
            spend=ToolSpendProfile(
                currency=STRIPE_REFUND_CURRENCY,
                amount_field="amount_minor",
                actual_amount_field="actual_refunded_minor",
            ),
        )
    ]
