"""Pre-call org spend-ceiling enforcement for the LLM egress (owner D1-D8).

This is the gate that refuses an Anthropic call BEFORE any provider egress when
the org's period-to-date LLM spend PLUS a conservative estimate of the pending
call's cost would breach the org-wide spend ceiling (``org_spend_ceiling``,
migration 0014). It is wired into BOTH egresses of ``AnthropicAdapter``
(``generate`` and ``generate_with_tools``).

HONEST GUARANTEE (owner decision, step 9 — do NOT describe this as a hard cap):
  This design does NOT guarantee the ceiling is never exceeded. The gate reads
  period-to-date spend, adds an estimate, and compares — but that read is NOT
  atomic with the ai_cost_ledger write that happens AFTER a served call, so
  concurrent in-flight calls can each pass the gate before any of their costs are
  recorded. What it DOES guarantee: overshoot is bounded by roughly one maximal
  in-flight call per concurrent run in the same org (i.e. at most about
  ``concurrency - 1`` extra maximal calls beyond the ceiling). It is a SOFT cap
  that bounds overshoot, not a hard cap. Making it hard would require reserving
  the estimate transactionally before the call and settling it after — out of
  scope here and noted as a future item.

The pending-call estimate (owner decisions, Phase 2):
  The real input-token count is unknown before the provider tokenizes the prompt.
  We deliberately DO NOT call the provider's token-counting API (it needs a live
  key and adds a network round trip to every call — step 6). Instead we estimate
  input tokens from the prompt's CHARACTER length with constants biased so the
  estimate ERRS HIGH (see ``_CHARS_PER_TOKEN_ESTIMATE`` /
  ``_INPUT_TOKEN_SAFETY_MULTIPLIER`` below): over-estimating can only refuse a
  borderline call (safe — nothing is overspent), while under-estimating would let
  a breaching call through (unsafe). Refusing is the safe direction.

  A repo tokenizer exists — ``skylize.memory.compression.budget.count_tokens``
  (tiktoken ``cl100k_base``) — but it is deliberately NOT used here: (1) the owner
  specified a character-based heuristic for this gate, and (2) ``cl100k_base`` is
  a GPT tokenizer, not Anthropic's, so it can UNDER-count for Claude models — the
  unsafe direction for a spend gate.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from typing import TYPE_CHECKING, Callable, NoReturn
from uuid import UUID

from ...schemas.events.governance import GovernanceScopeViolation

if TYPE_CHECKING:
    from ...app.audit.service import AuditService
    from ...dal.cost_ledger import CostLedgerDAL, PriceSnapshot
    from ...dal.org_spend_ceiling import OrgSpendCeilingDAL
    from ...events.bus import EventBus

# --- Pending-call input-token estimate constants (Phase 2, step 7) -----------
# Every constant here is chosen to bias the input-token estimate UP, because for
# a spend gate the safe direction is to over-estimate:
#   over-estimate cost -> may REFUSE a borderline call (safe: nothing overspent);
#   under-estimate cost -> may ALLOW a breaching call (unsafe: overspend).
#
# English text averages ~4 characters per token; dividing character length by a
# SMALLER number therefore yields MORE estimated tokens (bias high).
_CHARS_PER_TOKEN_ESTIMATE = 3.0  # < the ~4 real average, so this OVER-counts tokens
# Extra headroom multiplied onto the character-based token count. > 1.0 biases the
# estimate up, covering structure a raw character count under-represents (system
# prompt, tool schemas, message framing).
_INPUT_TOKEN_SAFETY_MULTIPLIER = 1.15

_MICROS_PER_MTOK = Decimal(1_000_000)  # prices are micro-USD per 1e6 tokens


def estimate_input_tokens(input_chars: int) -> int:
    """Conservative (biased-high) input-token estimate from a character count.

    Uses ``_CHARS_PER_TOKEN_ESTIMATE`` and ``_INPUT_TOKEN_SAFETY_MULTIPLIER`` and
    rounds UP, so the returned token count is always >= a naive 4-chars/token
    estimate. Over-counting is the safe direction for a spend gate (see module
    docstring).
    """
    if input_chars <= 0:
        return 0
    return math.ceil(input_chars / _CHARS_PER_TOKEN_ESTIMATE * _INPUT_TOKEN_SAFETY_MULTIPLIER)


def estimate_max_micros(
    *,
    input_chars: int,
    requested_max_tokens: int,
    input_price_micros_per_mtok: int,
    output_price_micros_per_mtok: int,
) -> int:
    """The pending call's estimated MAXIMUM cost in micro-USD (Phase 2, step 7).

        estimated_max_micros = estimated_input_tokens * input_rate
                             + requested_max_tokens   * output_rate

    where the rates are the SAME model_pricing micro-USD-per-Mtok prices the cost
    ledger will use to record the actual charge (so the estimate and the eventual
    ai_cost_ledger row are in one unit — micro-USD, owner decision D3). Input
    tokens are over-estimated from ``input_chars`` (see ``estimate_input_tokens``)
    and output tokens are pinned to ``requested_max_tokens`` — the most the call
    can produce. The division back from per-Mtok is rounded toward +infinity
    (ROUND_CEILING), deliberately unlike the ledger's HALF-UP, to keep the bias
    high.
    """
    est_input_tokens = estimate_input_tokens(input_chars)
    gross = (
        Decimal(est_input_tokens) * Decimal(input_price_micros_per_mtok)
        + Decimal(requested_max_tokens) * Decimal(output_price_micros_per_mtok)
    )
    micros = (gross / _MICROS_PER_MTOK).to_integral_value(rounding=ROUND_CEILING)
    return int(micros)


class OrgSpendCeilingExceeded(Exception):
    """A call was REFUSED before egress by the org spend-ceiling gate.

    Raised in two cases, both of which refuse the call: (a) no ceiling row exists
    for (org, current period) — fail closed (owner decision D6); or (b) the
    estimated post-call spend would breach the configured ceiling. Carries the
    full decision context (all micro-USD) so the refusal is auditable.
    ``ceiling_micros`` is ``None`` in case (a) — no ceiling was configured.
    """

    def __init__(
        self,
        *,
        org_id: str,
        billing_period: str,
        ceiling_micros: int | None,
        period_to_date_micros: int,
        estimated_micros: int,
        reason: str,
    ) -> None:
        self.org_id = org_id
        self.billing_period = billing_period
        self.ceiling_micros = ceiling_micros
        self.period_to_date_micros = period_to_date_micros
        self.estimated_micros = estimated_micros
        self.reason = reason
        super().__init__(reason)


class SpendCeilingEnforcer:
    """Pre-call org spend-ceiling gate. Wired at BOTH LLM egresses.

    Holds the ceiling DAL, the cost ledger DAL (for the org-wide period
    aggregate), an ``AuditService``, and the ``EventBus``. On refusal it writes an
    audit record AND emits an existing governance event
    (``GovernanceScopeViolation`` with ``failed_stage="budget"`` — the closed
    governance taxonomy already models a budget-stage tool denial; no new event
    type or stream is introduced), then raises ``OrgSpendCeilingExceeded``.
    """

    def __init__(
        self,
        *,
        ceiling_dal: "OrgSpendCeilingDAL",
        cost_ledger: "CostLedgerDAL",
        audit: "AuditService",
        bus: "EventBus",
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._ceiling_dal = ceiling_dal
        self._cost_ledger = cost_ledger
        self._audit = audit
        self._bus = bus
        # Same clock source the ledger uses to stamp billing_period
        # (anthropic_adapter._settle_cost: datetime.now(timezone.utc), then
        # strftime("%Y-%m")). Injectable so tests can pin the period.
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def enforce(
        self,
        *,
        org_id: str,
        agent_id: str,
        governance_token_id: UUID,
        correlation_id: UUID,
        attempted_tool: str,
        input_chars: int,
        requested_max_tokens: int,
        price: "PriceSnapshot",
    ) -> None:
        """Refuse before egress if this call would breach the org ceiling.

        Resolves the current ``billing_period`` from the ledger's clock, reads the
        ceiling (missing row => refuse, D6), reads the org-wide period-to-date
        spend across ALL providers (D8), computes the biased-high pending estimate
        (Phase 2), and refuses when ``period_to_date + estimate > ceiling``. On
        refusal an audit record and a governance event are emitted and
        ``OrgSpendCeilingExceeded`` is raised — the caller must NOT reach the SDK.
        """
        billing_period = self._now().strftime("%Y-%m")
        ceiling_micros = await self._ceiling_dal.read_ceiling_micros(org_id, billing_period)
        period_to_date = await self._cost_ledger.org_period_total_micros(org_id, billing_period)
        estimate = estimate_max_micros(
            input_chars=input_chars,
            requested_max_tokens=requested_max_tokens,
            input_price_micros_per_mtok=price.input_price_micros_per_mtok,
            output_price_micros_per_mtok=price.output_price_micros_per_mtok,
        )

        if ceiling_micros is None:
            await self._refuse(
                org_id=org_id,
                agent_id=agent_id,
                governance_token_id=governance_token_id,
                correlation_id=correlation_id,
                attempted_tool=attempted_tool,
                billing_period=billing_period,
                ceiling_micros=None,
                period_to_date=period_to_date,
                estimate=estimate,
                reason=(
                    f"no org spend ceiling configured for org={org_id!r} "
                    f"period={billing_period!r}; failing closed (D6)"
                ),
            )

        if period_to_date + estimate > ceiling_micros:
            await self._refuse(
                org_id=org_id,
                agent_id=agent_id,
                governance_token_id=governance_token_id,
                correlation_id=correlation_id,
                attempted_tool=attempted_tool,
                billing_period=billing_period,
                ceiling_micros=ceiling_micros,
                period_to_date=period_to_date,
                estimate=estimate,
                reason=(
                    f"projected org spend {period_to_date + estimate} micro-USD "
                    f"(period_to_date={period_to_date} + estimate={estimate}) exceeds "
                    f"ceiling={ceiling_micros} micro-USD for org={org_id!r} "
                    f"period={billing_period!r}"
                ),
            )

    async def _refuse(
        self,
        *,
        org_id: str,
        agent_id: str,
        governance_token_id: UUID,
        correlation_id: UUID,
        attempted_tool: str,
        billing_period: str,
        ceiling_micros: int | None,
        period_to_date: int,
        estimate: int,
        reason: str,
    ) -> NoReturn:
        """Record the refusal (audit + governance event) and raise. Never returns.

        Two writes, mirroring how the Governance Authority records a governed
        refusal: an ``AuditService`` record (append-only ``audit_log`` row +
        ``audit.action_recorded`` on the bus) and a ``GovernanceScopeViolation``
        governance event at the ``budget`` stage. No ai_cost_ledger row is written
        anywhere on this path — the call is refused before the SDK is touched, so
        nothing was spent (owner decision, step 16).
        """
        await self._audit.record(
            org_id=org_id,
            correlation_id=correlation_id,
            action_type="governance.spend_ceiling_exceeded",
            result="denied",
            source_agent_id=agent_id,
            governance_token_id=governance_token_id,
            result_reason=reason,
        )
        await self._bus.publish(
            GovernanceScopeViolation(
                tenant_id=org_id,
                partition_key=f"agent:{agent_id}",
                department="governance",
                source_agent_id=agent_id,
                governance_token_id=governance_token_id,
                correlation_id=correlation_id,
                payload=GovernanceScopeViolation.Payload(
                    token_id=governance_token_id,
                    agent_id=agent_id,
                    attempted_tool=attempted_tool,
                    failed_stage="budget",
                    reason=reason,
                ),
            )
        )
        raise OrgSpendCeilingExceeded(
            org_id=org_id,
            billing_period=billing_period,
            ceiling_micros=ceiling_micros,
            period_to_date_micros=period_to_date,
            estimated_micros=estimate,
            reason=reason,
        )
