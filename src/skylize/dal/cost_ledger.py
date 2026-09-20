"""Cost Ledger DAL — billing-grade LLM cost attribution (ADR-0006).

This is the ``dal`` layer (NOT ``runtime``: the import-linter contract
"Pure inner layers hold no database driver" forbids asyncpg in
``skylize.runtime``). The concrete LLM gateway adapter — the single point where
real provider usage is first observed — will call this DAL at wiring time
(T-B1B); this module ships the contract and is proven in isolation.

Money discipline (ADR-0006 §"Money & rounding"):
  * Cost is stored in ``cost_micros`` — millionths of one currency unit, matching
    the gateway's ``LLMGenerateResponse.cost_usd_micros`` contract. Cents are
    DERIVED once at aggregation, never stored per row, so small-call residue
    never drifts.
  * All money arithmetic uses ``Decimal`` with ``ROUND_HALF_UP`` — never float.
  * Unit prices are per 1e6 tokens (``*_per_mtok``) so every real quoted price is
    an exact integer and ``input_tokens * price`` needs no fractional price.

Every query runs inside ``Database.tenant_session(org_id)`` so the RLS
``tenant_isolation`` policy on ``ai_cost_ledger`` applies — tenant isolation
holds at the data layer regardless of upstream checks. ``model_pricing`` is
platform reference data (no RLS); reads filter global-vs-tenant explicitly.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from skylize.dal.connection import Database

# 1e6 tokens per "Mtok" price unit; 1e6 micros per one currency unit;
# 1e4 micros per one minor unit (cent).
_MICROS_PER_MTOK = Decimal(1_000_000)
_MICROS_PER_UNIT = Decimal(1_000_000)
_MICROS_PER_MINOR = Decimal(10_000)


class PricingNotFound(RuntimeError):
    """No active ``model_pricing`` row covers this (provider, model, org, time)."""


class LedgerEntryNotFound(RuntimeError):
    """The charge entry targeted for reversal does not exist for this tenant."""


# ---------------------------------------------------------------------------
# Pure money math — no I/O, unit-testable in isolation (see ADR-0006 §rounding).
# ---------------------------------------------------------------------------

def compute_cost_micros(
    *,
    input_tokens: int,
    output_tokens: int,
    input_price_micros_per_mtok: int,
    output_price_micros_per_mtok: int,
) -> int:
    """Exact per-call cost in micro-currency (millionths of one currency unit).

    cost = (in_tok * in_price + out_tok * out_price) / 1e6, rounded HALF-UP to the
    nearest whole micro. The residue is the sub-micro fraction (|error| < 0.5
    micro = < 5e-7 of one currency unit per row); it goes into that nearest-micro
    rounding — never truncated, never floated. Because a micro is 100x finer than
    a cent, per-row rounding cannot perturb any cent-level total; invoice cents are
    produced ONCE, at aggregation (see ``micros_to_minor``).
    """
    gross = (
        Decimal(input_tokens) * Decimal(input_price_micros_per_mtok)
        + Decimal(output_tokens) * Decimal(output_price_micros_per_mtok)
    )
    micros = (gross / _MICROS_PER_MTOK).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(micros)


def micros_to_unit(cost_micros: int) -> Decimal:
    """Micro-currency -> currency units as an exact Decimal (e.g. 3 -> 0.000003)."""
    return Decimal(cost_micros) / _MICROS_PER_UNIT


def micros_to_minor(cost_micros: int) -> Decimal:
    """Micro-currency -> minor units (cents), rounded HALF-UP exactly once.

    This is the ONLY rounding to cents in the money path; call it on an aggregate
    (a period SUM), not per row, so reconciliation stays exact.
    """
    return (Decimal(cost_micros) / _MICROS_PER_MINOR).quantize(
        Decimal(1), rounding=ROUND_HALF_UP
    )


# ---------------------------------------------------------------------------
# Boundary models (Pydantic v2) — no dict crosses the DAL boundary.
# ---------------------------------------------------------------------------

class CostObservation(BaseModel):
    """One real provider call as observed at the seam (the gateway adapter).

    Carries every attribution key + raw token usage; the DAL resolves the price
    snapshot and computes money. ``idempotency_key`` is the provider's
    response/request id for the served call (globally unique per real charge) so a
    retried WRITE collapses to one row.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    org_id: str
    correlation_id: UUID
    agent_id: str
    run_id: UUID                       # governance token id == run key
    step_id: UUID | None = None        # tool-use turn within the run
    provider: str
    model: str                         # concrete provider model id
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    occurred_at: datetime
    billing_period: str                # e.g. "2026-07"
    idempotency_key: str
    byok: bool = False
    provider_invoice_ref: str | None = None


class PriceSnapshot(BaseModel):
    """The active price the DAL resolved and froze onto a ledger row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_price_micros_per_mtok: int = Field(ge=0)
    output_price_micros_per_mtok: int = Field(ge=0)
    pricing_version: int
    currency: str


class PeriodSpend(BaseModel):
    """One billing period's org-wide totals — the console's usage history row.

    Every number is an EXACT integer: ``cost_micros`` is micro-currency (ADR-0006),
    NOT cents and NOT the minor units that ``budget_ledger`` uses. Token counts are
    raw provider tokens. All three are charges NET of reversals, so a reversed
    period can legitimately report a smaller (or negative) total.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    billing_period: str                # "%Y-%m", e.g. "2026-07"
    cost_micros: int
    input_tokens: int
    output_tokens: int


class ModelSpend(BaseModel):
    """One (provider, model) pair's totals inside a single billing period.

    Same unit discipline as ``PeriodSpend``: ``cost_micros`` is micro-currency.
    ``currency`` comes from the rows themselves — it is never assumed to be USD.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    cost_micros: int
    input_tokens: int
    output_tokens: int
    currency: str


class CostRecord(BaseModel):
    """Result of recording (or reversing) one entry. Money is Decimal, never float."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: UUID
    cost_micros: int
    currency: str
    inserted: bool  # False when idempotency collapsed a retried write

    @property
    def cost(self) -> Decimal:
        """Cost in currency units as an exact Decimal."""
        return micros_to_unit(self.cost_micros)


# ---------------------------------------------------------------------------
# DAL
# ---------------------------------------------------------------------------

class CostLedgerDAL:
    def __init__(self, db: "Database") -> None:
        self._db = db

    async def resolve_price(self, obs: CostObservation) -> PriceSnapshot:
        """Resolve the active price for one observation (see resolve_price_for)."""
        return await self.resolve_price_for(
            org_id=obs.org_id,
            provider=obs.provider,
            model=obs.model,
            occurred_at=obs.occurred_at,
        )

    async def resolve_price_for(
        self, *, org_id: str, provider: str, model: str, occurred_at: datetime
    ) -> PriceSnapshot:
        """Resolve the active price for (provider, model) at ``occurred_at``.

        Takes bare attribution keys (no token counts) so the gateway adapter
        can run its PRE-CALL pricing gate before any usage exists to observe.

        Global-key-fallback shape (ADR-0006 §BYOK): a tenant-specific row
        (org_id = tenant) wins over the global row (org_id IS NULL); among
        matches the latest ``effective_from`` covering ``occurred_at`` wins.
        Raises ``PricingNotFound`` if nothing covers the point in time.
        """
        async with self._db.tenant_session(org_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT input_price_micros_per_mtok,
                       output_price_micros_per_mtok,
                       version,
                       currency
                FROM model_pricing
                WHERE provider = $1
                  AND model = $2
                  AND (org_id = $3 OR org_id IS NULL)
                  AND effective_from <= $4
                  AND (effective_to IS NULL OR effective_to > $4)
                ORDER BY (org_id IS NULL), effective_from DESC
                LIMIT 1
                """,
                provider,
                model,
                org_id,
                occurred_at,
            )
        if row is None:
            raise PricingNotFound(
                f"No model_pricing for provider={provider!r} model={model!r} "
                f"org={org_id!r} at {occurred_at.isoformat()}"
            )
        return PriceSnapshot(
            input_price_micros_per_mtok=row["input_price_micros_per_mtok"],
            output_price_micros_per_mtok=row["output_price_micros_per_mtok"],
            pricing_version=row["version"],
            currency=row["currency"],
        )

    async def record_cost(
        self, obs: CostObservation, *, price: PriceSnapshot | None = None
    ) -> CostRecord:
        """Record one immutable cost row transactionally.

        Resolves + snapshots the price, computes ``cost_micros`` (Decimal,
        HALF-UP), and INSERTs with ``ON CONFLICT (org_id, idempotency_key) DO
        NOTHING`` — a retried call collapses to the single existing row
        (``inserted=False``). A retried LLM call never double-charges.

        ``price`` — OPTIONAL pre-resolved snapshot (owner decision DEC-A: resolve
        the price ONCE per call). The gateway adapter's pre-call pricing gate has
        already resolved a price for this exact call, and ``obs.model`` is the
        provider's RESOLVED model id, which can differ from the id the gate
        priced (Anthropic resolves aliases). Re-resolving from ``obs.model``
        therefore risks two outcomes, both wrong AFTER the provider has already
        billed: a DIFFERENT rate than the one the spend ceiling was checked
        against, or ``PricingNotFound`` — which would abort the write and lose
        the record of money already owed. Passing the gate's snapshot makes it
        the single price for the estimate, the returned cost, and this row.

        Omitted (the default), behaviour is exactly as before: the price is
        resolved here from ``obs``. Existing callers are unaffected.
        """
        if price is None:
            price = await self.resolve_price(obs)
        cost_micros = compute_cost_micros(
            input_tokens=obs.input_tokens,
            output_tokens=obs.output_tokens,
            input_price_micros_per_mtok=price.input_price_micros_per_mtok,
            output_price_micros_per_mtok=price.output_price_micros_per_mtok,
        )
        async with self._db.tenant_session(obs.org_id) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO ai_cost_ledger (
                    org_id, correlation_id, agent_id, run_id, step_id,
                    provider, model, input_tokens, output_tokens,
                    input_price_micros_per_mtok, output_price_micros_per_mtok,
                    pricing_version, cost_micros, currency, byok,
                    billing_period, provider_invoice_ref, idempotency_key,
                    entry_type, occurred_at
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,
                        $16,$17,$18,'charge',$19)
                ON CONFLICT (org_id, idempotency_key) DO NOTHING
                RETURNING entry_id, cost_micros, currency
                """,
                obs.org_id,
                obs.correlation_id,
                obs.agent_id,
                obs.run_id,
                obs.step_id,
                obs.provider,
                obs.model,
                obs.input_tokens,
                obs.output_tokens,
                price.input_price_micros_per_mtok,
                price.output_price_micros_per_mtok,
                price.pricing_version,
                cost_micros,
                price.currency,
                obs.byok,
                obs.billing_period,
                obs.provider_invoice_ref,
                obs.idempotency_key,
                obs.occurred_at,
            )
            if row is None:
                # Idempotency collapsed a retried write — return the row that won.
                existing = await conn.fetchrow(
                    """
                    SELECT entry_id, cost_micros, currency
                    FROM ai_cost_ledger
                    WHERE org_id = $1 AND idempotency_key = $2
                    """,
                    obs.org_id,
                    obs.idempotency_key,
                )
                assert existing is not None  # the conflicting row must exist
                return CostRecord(
                    entry_id=existing["entry_id"],
                    cost_micros=existing["cost_micros"],
                    currency=existing["currency"],
                    inserted=False,
                )
        return CostRecord(
            entry_id=row["entry_id"],
            cost_micros=row["cost_micros"],
            currency=row["currency"],
            inserted=True,
        )

    async def reverse_entry(
        self,
        org_id: str,
        entry_id: UUID,
        *,
        idempotency_key: str,
    ) -> CostRecord:
        """Append a reversal (negated tokens/cost) of a prior charge.

        Corrections never UPDATE history — the DB rejects that (append-only guard).
        The reversal carries its own ``idempotency_key`` so it, too, is recorded at
        most once. Raises ``LedgerEntryNotFound`` if the charge is absent.
        """
        async with self._db.tenant_session(org_id) as conn:
            orig = await conn.fetchrow(
                """
                SELECT correlation_id, agent_id, run_id, step_id, provider, model,
                       input_tokens, output_tokens, input_price_micros_per_mtok,
                       output_price_micros_per_mtok, pricing_version, cost_micros,
                       currency, byok, billing_period, provider_invoice_ref, occurred_at
                FROM ai_cost_ledger
                WHERE entry_id = $1 AND entry_type = 'charge'
                """,
                entry_id,
            )
            if orig is None:
                raise LedgerEntryNotFound(
                    f"No charge entry {entry_id} for org={org_id!r} to reverse"
                )
            row = await conn.fetchrow(
                """
                INSERT INTO ai_cost_ledger (
                    org_id, correlation_id, agent_id, run_id, step_id,
                    provider, model, input_tokens, output_tokens,
                    input_price_micros_per_mtok, output_price_micros_per_mtok,
                    pricing_version, cost_micros, currency, byok,
                    billing_period, provider_invoice_ref, idempotency_key,
                    entry_type, reverses_entry_id, occurred_at
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,
                        $16,$17,$18,'reversal',$19,$20)
                ON CONFLICT (org_id, idempotency_key) DO NOTHING
                RETURNING entry_id, cost_micros, currency
                """,
                org_id,
                orig["correlation_id"],
                orig["agent_id"],
                orig["run_id"],
                orig["step_id"],
                orig["provider"],
                orig["model"],
                -orig["input_tokens"],
                -orig["output_tokens"],
                orig["input_price_micros_per_mtok"],
                orig["output_price_micros_per_mtok"],
                orig["pricing_version"],
                -orig["cost_micros"],
                orig["currency"],
                orig["byok"],
                orig["billing_period"],
                orig["provider_invoice_ref"],
                idempotency_key,
                entry_id,
                orig["occurred_at"],
            )
            if row is None:
                existing = await conn.fetchrow(
                    """
                    SELECT entry_id, cost_micros, currency
                    FROM ai_cost_ledger
                    WHERE org_id = $1 AND idempotency_key = $2
                    """,
                    org_id,
                    idempotency_key,
                )
                assert existing is not None
                return CostRecord(
                    entry_id=existing["entry_id"],
                    cost_micros=existing["cost_micros"],
                    currency=existing["currency"],
                    inserted=False,
                )
        return CostRecord(
            entry_id=row["entry_id"],
            cost_micros=row["cost_micros"],
            currency=row["currency"],
            inserted=True,
        )

    async def period_total_micros(
        self, org_id: str, provider: str, billing_period: str
    ) -> int:
        """SUM of ``cost_micros`` for a provider + period (charges net of reversals).

        Exact integer addition — the reconciliation-grade total against a provider
        invoice line. Round to minor units ONCE via ``micros_to_minor``.
        """
        async with self._db.tenant_session(org_id) as conn:
            total = await conn.fetchval(
                """
                SELECT COALESCE(SUM(cost_micros), 0)
                FROM ai_cost_ledger
                WHERE provider = $1 AND billing_period = $2
                """,
                provider,
                billing_period,
            )
        return int(total)

    async def period_total(
        self, org_id: str, provider: str, billing_period: str
    ) -> Decimal:
        """Period total in currency units as an exact Decimal (never float)."""
        return micros_to_unit(
            await self.period_total_micros(org_id, provider, billing_period)
        )

    async def run_total_micros(self, org_id: str, correlation_id: UUID) -> int:
        """SUM of ``cost_micros`` for ONE run (charges net of reversals).

        The money value of every LLM call made under a single correlation id --
        which is what an autonomous run needs to put a truthful `cost_minor` on
        its `work_journal` row (app/autonomy/runner.py). Deliberately keyed on
        ``correlation_id`` rather than ``run_id``:

          * ``run_id`` is the governance TOKEN id (cost_ledger.py:114). A run that
            fails before the deliverable exists exposes no token id to the caller,
            so a token-keyed read could not price a failed run at all;
          * ``correlation_id`` is owned by the caller (execute()'s
            ``correlation_id`` parameter) and is stamped on every ledger row the
            run produces, so it prices the success AND failure paths identically.

        Structurally IDENTICAL to ``org_period_total_micros`` -- tenant_session so
        RLS scopes the SUM, ``cost_micros`` so reversals net out, COALESCE so a
        run that spent nothing totals 0, exact integer micro-currency. Round to
        minor units ONCE, via ``micros_to_minor``, at the call site.

        Indexed by ``idx_ai_cost_ledger_org_correlation`` (migration 0029):
        ``(org_id, correlation_id) INCLUDE (cost_micros)``. ``org_id`` leads
        because ``tenant_session`` puts the RLS predicate on this table into the
        search, and INCLUDE keeps the SUM an index-only scan. Before 0029 this
        read was a sequential scan of the whole ledger -- fine at the pilot's one
        agent, not fine once the fleet is scheduled.
        """
        async with self._db.tenant_session(org_id) as conn:
            total = await conn.fetchval(
                """
                SELECT COALESCE(SUM(cost_micros), 0)
                FROM ai_cost_ledger
                WHERE correlation_id = $1
                """,
                correlation_id,
            )
        return int(total)

    async def run_total_minor(self, org_id: str, correlation_id: UUID) -> int:
        """One run's cost in MINOR units (cents), rounded exactly once.

        The form `skylize.app` consumes (app/autonomy/runner.py writes it to
        `work_journal.cost_minor`). It exists as a DAL method, rather than the app
        layer calling ``micros_to_minor`` on ``run_total_micros`` itself, for two
        reasons that point the same way:

          * the import-linter contract "Application logic contains no SQL" forbids
            `skylize.app` from importing `skylize.dal.cost_ledger` at all -- that
            module reaches `dal.connection` and therefore asyncpg. Money helpers
            are not exportable across that boundary;
          * ADR-0006 makes the cents conversion "the ONLY rounding to cents in the
            money path". Keeping it inside the ledger means the app layer cannot
            grow a second, subtly different copy of it.

        Rounds the AGGREGATE, never per row, exactly as ``micros_to_minor``
        requires. Returns the signed value: a run whose charges were reversed nets
        negative, and clamping is the caller's constraint to apply, not a truth the
        ledger should hide.
        """
        return int(micros_to_minor(await self.run_total_micros(org_id, correlation_id)))

    async def org_period_total_micros(
        self, org_id: str, billing_period: str
    ) -> int:
        """Org-wide SUM of ``cost_micros`` across ALL providers for a period.

        The org spend ceiling is ORG-WIDE, across every provider (owner decision
        D8), so the aggregate it checks against must NOT be provider-scoped. This
        is ``period_total_micros`` with the ``provider`` filter dropped and
        otherwise IDENTICAL:

          * kept identical — runs inside ``tenant_session(org_id)`` so the RLS
            ``tenant_isolation`` policy scopes the SUM to this org; sums
            ``cost_micros`` (charges NET of reversals, since reversals are
            negative rows); ``COALESCE(..., 0)`` so an org with no rows totals 0;
            returns an exact integer (micro-USD), never a float;
          * changed — WHERE has no ``provider`` predicate, so the total spans
            every provider in the period.

        The existing provider-scoped ``period_total_micros`` is left untouched —
        other callers (reconciliation against a single provider invoice line) rely
        on it.
        """
        async with self._db.tenant_session(org_id) as conn:
            total = await conn.fetchval(
                """
                SELECT COALESCE(SUM(cost_micros), 0)
                FROM ai_cost_ledger
                WHERE billing_period = $1
                """,
                billing_period,
            )
        return int(total)

    async def org_period_history(
        self, org_id: str, *, limit: int = 12
    ) -> list[PeriodSpend]:
        """Org-wide spend + token totals GROUPED BY billing_period, newest first.

        The console's usage history. Structurally the GROUP BY form of
        ``org_period_total_micros`` and scoped identically:

          * runs inside ``tenant_session(org_id)`` so the RLS ``tenant_isolation``
            policy on ``ai_cost_ledger`` scopes every group to this org — the
            query itself carries NO ``org_id`` predicate, exactly as the existing
            period aggregate does;
          * sums ``cost_micros`` so reversals net out of their own period (a
            reversal carries the ORIGINAL row's ``billing_period``, cost_ledger
            reverse_entry, so a correction lands in the period it corrects, never
            in the month the correction was made);
          * exact integer micro-currency, never a float, never pre-rounded to
            cents. Cents are derived ONCE at the display boundary via
            ``micros_to_minor`` (ADR-0006 §"Money & rounding").

        ``billing_period`` is a zero-padded ``"%Y-%m"`` string, so DESC text order
        IS reverse chronological order — no date parsing in SQL.

        Served by ``idx_ai_cost_ledger_org_period`` (migration 0016,
        ``(org_id, billing_period) INCLUDE (cost_micros)``) for the leading key and
        the money column; the token columns are heap fetches, which is acceptable
        because this read is a console page load, not the LLM hot path that index
        was measured for.

        ``limit`` bounds the answer so a long-lived org cannot return an unbounded
        list; it must be >= 1.
        """
        if limit < 1:
            raise ValueError(f"limit must be >= 1; got {limit}")
        async with self._db.tenant_session(org_id) as conn:
            rows = await conn.fetch(
                """
                SELECT billing_period,
                       COALESCE(SUM(cost_micros), 0)   AS cost_micros,
                       COALESCE(SUM(input_tokens), 0)  AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens
                FROM ai_cost_ledger
                GROUP BY billing_period
                ORDER BY billing_period DESC
                LIMIT $1
                """,
                limit,
            )
        return [
            PeriodSpend(
                billing_period=row["billing_period"],
                cost_micros=int(row["cost_micros"]),
                input_tokens=int(row["input_tokens"]),
                output_tokens=int(row["output_tokens"]),
            )
            for row in rows
        ]

    async def org_period_model_breakdown(
        self, org_id: str, billing_period: str
    ) -> list[ModelSpend]:
        """Per-(provider, model) totals WITHIN one billing period, dearest first.

        The "what did the money go on" half of the console's usage read. Scoped
        exactly like ``org_period_history``: ``tenant_session`` so RLS supplies the
        org predicate, ``SUM(cost_micros)`` so reversals net out, exact integers in
        micro-currency.

        ``currency`` is read from the rows rather than assumed: the ledger stores
        it per row (migration 0012) and grouping by it means a period that somehow
        held two currencies yields two DISTINCT rows instead of one silently
        summed nonsense total. Nothing here converts between currencies.

        Ordered by ``cost_micros DESC`` then ``(provider, model)`` so the ordering
        is TOTAL and the output is deterministic when several models cost the same
        (including the all-zero case a fresh environment produces — see below).

        NOTE on zeros: ``model_pricing`` ships deliberately EMPTY (migration 0012
        §"Seed"), so in an environment where ops has not seeded prices there are no
        ledger rows at all and this returns ``[]``. That empty list is the honest
        answer, not a failure to report.

        Served by ``idx_ai_cost_ledger_reconcile`` (``(org_id, provider,
        billing_period)``, migration 0012).
        """
        async with self._db.tenant_session(org_id) as conn:
            rows = await conn.fetch(
                """
                SELECT provider,
                       model,
                       currency,
                       COALESCE(SUM(cost_micros), 0)   AS cost_micros,
                       COALESCE(SUM(input_tokens), 0)  AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens
                FROM ai_cost_ledger
                WHERE billing_period = $1
                GROUP BY provider, model, currency
                ORDER BY cost_micros DESC, provider ASC, model ASC
                """,
                billing_period,
            )
        return [
            ModelSpend(
                provider=row["provider"],
                model=row["model"],
                cost_micros=int(row["cost_micros"]),
                input_tokens=int(row["input_tokens"]),
                output_tokens=int(row["output_tokens"]),
                currency=row["currency"],
            )
            for row in rows
        ]
