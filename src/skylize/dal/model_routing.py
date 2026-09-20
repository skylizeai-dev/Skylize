"""Model Routing DAL — the ORG-SCOPED logical-model routing policy.

Read/write layer for ``model_routing_rules`` (migration 0032): which logical
model each routing class resolves to for one org, and what it falls back to
when the target is refused.

TWO READS, AND THEY ANSWER DIFFERENT QUESTIONS.

``read_rules`` answers "what has this org configured". It is EFFECTIVE-DATED,
the shape ``OrgAutonomyModeDAL.read_configured_mode`` established: the rule set
in force is every row at the single greatest ``effective_from`` at or before the
requested instant, so one routing decision covering three classes reads back as
one generation and never as a merge of two.

``read_catalogue`` answers "what models exist at all", and it does NOT touch the
database. The logical -> concrete map is Settings-owned
(``config.py:311-313``) and is read by the adapter at construction
(``adapters/llm/anthropic_adapter.py:267-271``); this function reports THAT map,
so the console shows the concrete provider model id an org's traffic would
actually reach rather than a name someone typed into a table.

FAIL CLOSED. When no row resolves, ``resolve_class`` returns the IDENTITY
mapping: the class routes to itself, with no fallback. That is exactly what the
adapter does today with no routing table at all, so an org that has configured
nothing behaves identically to one before this table existed. It is not a
guess -- it is the only routing the running system can honestly claim.

PRICING IS NOT FABRICATED. ``read_catalogue`` reports a price only when
``model_pricing`` (migration 0012) actually has a row covering that concrete
model at the requested instant. That table is seeded EMPTY by design
(migration 0012's own Seed note: "Real provider prices are ops-managed and are
NOT fabricated here"), so on an unseeded deployment every entry comes back with
``pricing=None``. None means "nobody has priced this", which is a true
statement; any number here would not be.

Both database reads run inside ``Database.tenant_session(org_id)`` so the RLS
``tenant_isolation`` policy applies -- one org can never read or write another
org's routing -- exactly as ``OrgAutonomyModeDAL`` is scoped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from skylize.app.audit.service import AuditService
    from skylize.config import Settings
    from skylize.dal.connection import Database

#: The three logical model names the LLM gateway actually accepts, mirroring
#: the keys of ``AnthropicAdapter._model_map`` (anthropic_adapter.py:267-271),
#: the CHECK constraints in migration 0032, and the contract note in
#: ``adapters/llm/gateway.py:36``. A fourth name would raise ``ValueError`` at
#: ``_concrete_model`` (anthropic_adapter.py:368-374), so it is rejected here,
#: by the table's CHECK, and at egress.
LOGICAL_MODELS: tuple[str, ...] = ("default", "fast", "reasoning")

VALID_LOGICAL_MODELS: frozenset[str] = frozenset(LOGICAL_MODELS)

#: The provider every entry in the catalogue below belongs to. Pinned to the
#: adapter's own ``_PROVIDER`` constant (anthropic_adapter.py:218) rather than
#: retyped, because this string is the join key into ``model_pricing`` and
#: ``ai_cost_ledger`` (migration 0012) -- a typo would silently price nothing.
CATALOGUE_PROVIDER = "anthropic"


@dataclass(frozen=True)
class ModelPrice:
    """An actual ``model_pricing`` row, in the units migration 0012 stores.

    Micro-currency per 1e6 tokens, so every real quoted price is an exact
    integer and no float ever touches money. This object exists ONLY when a row
    was found; absence is reported as None, never as a zero.
    """

    input_price_micros_per_mtok: int
    output_price_micros_per_mtok: int
    currency: str
    pricing_version: int


@dataclass(frozen=True)
class CatalogueEntry:
    """One logical model, its concrete provider id, and its price if priced."""

    logical_name: str
    concrete_model: str
    provider: str
    pricing: ModelPrice | None


@dataclass(frozen=True)
class RoutingRule:
    """One configured class -> logical model mapping, at one instant."""

    routing_class: str
    target_logical_model: str
    fallback_logical_model: str | None
    effective_from: datetime
    #: False when no row exists and this is the identity mapping the read path
    #: supplies. The console needs this to tell an owner whether they have ever
    #: made this choice; routing must not branch on it.
    configured: bool


class ModelRoutingDAL:
    def __init__(self, db: "Database") -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Catalogue — Settings + model_pricing, never invented
    # ------------------------------------------------------------------

    async def read_catalogue(
        self,
        org_id: str,
        settings: "Settings",
        at: datetime | None = None,
    ) -> list[CatalogueEntry]:
        """The real logical -> concrete map, with a price only where one exists.

        The map itself comes from Settings and is the SAME map the adapter
        builds (anthropic_adapter.py:267-271), so the console cannot show a
        concrete model id that no request would ever reach.

        The price lookup mirrors ``CostLedgerDAL.resolve_price_for``
        (cost_ledger.py:171-215) exactly: a tenant-specific row wins over the
        global row, among matches the latest ``effective_from`` covering the
        instant wins. Unlike that method this one does NOT raise when nothing
        covers the point in time -- a console read is not an egress gate, and
        "unpriced" is a fact worth displaying rather than a 500.
        """
        moment = at if at is not None else datetime.now(timezone.utc)
        concrete = {
            "default": str(settings.llm_model_default),
            "fast": str(settings.llm_model_fast),
            "reasoning": str(settings.llm_model_reasoning),
        }
        prices = await self._read_prices(
            org_id, sorted(set(concrete.values())), moment
        )
        return [
            CatalogueEntry(
                logical_name=logical,
                concrete_model=concrete[logical],
                provider=CATALOGUE_PROVIDER,
                pricing=prices.get(concrete[logical]),
            )
            for logical in LOGICAL_MODELS
        ]

    async def _read_prices(
        self, org_id: str, models: list[str], at: datetime
    ) -> dict[str, ModelPrice]:
        """Active price per concrete model, omitting any that has none.

        One query for the whole catalogue rather than one per model: DISTINCT
        ON picks the winning row per model under the same ordering
        ``resolve_price_for`` uses -- ``(org_id IS NULL)`` ascending puts the
        tenant override ahead of the global row, then the latest
        ``effective_from``.
        """
        if not models:
            return {}
        async with self._db.tenant_session(org_id) as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (model)
                       model,
                       input_price_micros_per_mtok,
                       output_price_micros_per_mtok,
                       version,
                       currency
                FROM model_pricing
                WHERE provider = $1
                  AND model = ANY($2::text[])
                  AND (org_id = $3 OR org_id IS NULL)
                  AND effective_from <= $4
                  AND (effective_to IS NULL OR effective_to > $4)
                ORDER BY model, (org_id IS NULL), effective_from DESC
                """,
                CATALOGUE_PROVIDER,
                models,
                org_id,
                at,
            )
        return {
            row["model"]: ModelPrice(
                input_price_micros_per_mtok=row["input_price_micros_per_mtok"],
                output_price_micros_per_mtok=row["output_price_micros_per_mtok"],
                currency=row["currency"],
                pricing_version=row["version"],
            )
            for row in rows
        }

    # ------------------------------------------------------------------
    # Routing rules — effective-dated, fail closed to identity
    # ------------------------------------------------------------------

    async def read_rules(
        self, org_id: str, at: datetime | None = None
    ) -> list[RoutingRule]:
        """Every class's routing IN FORCE, filling unset classes with identity.

        Always returns one entry per name in ``LOGICAL_MODELS``, in that order,
        so a console never has to decide what an absent class means. A class
        with no configured row comes back as ``target == routing_class``,
        ``fallback is None``, ``configured=False``.

        Resolution semantics, mirroring the effective-dated autonomy read:
          * NO generation at or before ``at`` -> every class is identity.
          * a generation exists only EARLIER -> that generation is in force.
          * a NEWER generation supersedes an older one entirely.
          * a generation effective LATER than ``at`` is NOT used.
        """
        moment = at if at is not None else datetime.now(timezone.utc)
        configured = await self.read_configured_rules(org_id, moment)
        by_class = {rule.routing_class: rule for rule in configured}
        return [
            by_class.get(
                logical,
                RoutingRule(
                    routing_class=logical,
                    target_logical_model=logical,
                    fallback_logical_model=None,
                    effective_from=moment,
                    configured=False,
                ),
            )
            for logical in LOGICAL_MODELS
        ]

    async def read_configured_rules(
        self, org_id: str, at: datetime | None = None
    ) -> list[RoutingRule]:
        """Only the rows this org actually wrote; empty when it has written none.

        For DISPLAY and for the setter's before/after audit line. Enforcement
        and the console list should call ``read_rules``, which cannot leave a
        class unanswered.

        The subselect pins the generation FIRST so the outer query returns every
        class at one instant. Selecting ``ORDER BY effective_from DESC LIMIT 3``
        instead would silently splice two generations together whenever an org
        configured fewer than three classes in its latest decision.
        """
        moment = at if at is not None else datetime.now(timezone.utc)
        async with self._db.tenant_session(org_id) as conn:
            rows = await conn.fetch(
                """
                SELECT routing_class,
                       target_logical_model,
                       fallback_logical_model,
                       effective_from
                FROM model_routing_rules
                WHERE org_id = $1
                  AND effective_from = (
                      SELECT MAX(effective_from)
                      FROM model_routing_rules
                      WHERE org_id = $1 AND effective_from <= $2
                  )
                ORDER BY routing_class
                """,
                org_id,
                moment,
            )
        return [
            RoutingRule(
                routing_class=row["routing_class"],
                target_logical_model=row["target_logical_model"],
                fallback_logical_model=row["fallback_logical_model"],
                effective_from=row["effective_from"],
                configured=True,
            )
            for row in rows
        ]

    async def resolve_class(
        self, org_id: str, routing_class: str, at: datetime | None = None
    ) -> RoutingRule:
        """The routing in force for ONE class, fail-closed to identity.

        The single-class read an egress path would call. Fail closed here means
        the identity mapping and NO fallback: an org that configured nothing
        routes exactly as the adapter already routes, and an org that configured
        a target but no fallback gets a refusal rather than a silent spend on a
        model it did not choose.
        """
        if routing_class not in VALID_LOGICAL_MODELS:
            raise ValueError(
                f"routing_class must be one of {sorted(VALID_LOGICAL_MODELS)}; "
                f"got {routing_class!r}"
            )
        rules = await self.read_rules(org_id, at)
        return next(rule for rule in rules if rule.routing_class == routing_class)

    async def set_rules(
        self,
        *,
        org_id: str,
        rules: dict[str, tuple[str, str | None]],
        audit: "AuditService",
        correlation_id: UUID,
        effective_from: datetime | None = None,
        source_agent_id: str | None = None,
        governance_token_id: UUID | None = None,
    ) -> list[RoutingRule]:
        """Write ONE generation of routing rules, effective at ``effective_from``.

        ``rules`` maps routing class -> (target, fallback-or-None). Every entry
        lands at the SAME ``effective_from``, which is what makes the set one
        atomic decision the read can resolve as a generation.

        A routing change is a GOVERNANCE EVENT, not silent config -- the same
        rule ``OrgAutonomyModeDAL.set_mode`` follows for the autonomy posture
        and ``OrgSpendCeilingDAL.set_ceiling`` for the spend ceiling. It decides
        which priced model an org's spend is incurred against, so the
        append-only trail records who changed it and from what.

        Values are validated here as well as by the table's CHECKs so a bad
        name fails with a legible error instead of a constraint violation.
        Returns the generation now in force at ``effective_from``.
        """
        if not rules:
            raise ValueError("rules must name at least one routing class")
        for routing_class, (target, fallback) in sorted(rules.items()):
            if routing_class not in VALID_LOGICAL_MODELS:
                raise ValueError(
                    f"routing_class must be one of {sorted(VALID_LOGICAL_MODELS)}; "
                    f"got {routing_class!r}"
                )
            if target not in VALID_LOGICAL_MODELS:
                raise ValueError(
                    f"target_logical_model must be one of "
                    f"{sorted(VALID_LOGICAL_MODELS)}; got {target!r}"
                )
            if fallback is not None and fallback not in VALID_LOGICAL_MODELS:
                raise ValueError(
                    f"fallback_logical_model must be one of "
                    f"{sorted(VALID_LOGICAL_MODELS)} or None; got {fallback!r}"
                )
            if fallback is not None and fallback == target:
                raise ValueError(
                    f"fallback_logical_model must differ from "
                    f"target_logical_model; both are {target!r}"
                )
        moment = (
            effective_from if effective_from is not None else datetime.now(timezone.utc)
        )
        previous = await self.read_configured_rules(org_id, moment)
        async with self._db.tenant_session(org_id) as conn:
            for routing_class, (target, fallback) in sorted(rules.items()):
                await conn.execute(
                    """
                    INSERT INTO model_routing_rules (
                        org_id, effective_from, routing_class,
                        target_logical_model, fallback_logical_model
                    )
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (org_id, effective_from, routing_class)
                    DO UPDATE SET target_logical_model = EXCLUDED.target_logical_model,
                                  fallback_logical_model = EXCLUDED.fallback_logical_model,
                                  updated_at = now()
                    """,
                    org_id,
                    moment,
                    routing_class,
                    target,
                    fallback,
                )
        before = {r.routing_class: r.target_logical_model for r in previous}
        after = {cls: target for cls, (target, _fb) in rules.items()}
        await audit.record(
            org_id=org_id,
            correlation_id=correlation_id,
            action_type="governance.model_routing_set",
            result="success",
            source_agent_id=source_agent_id,
            governance_token_id=governance_token_id,
            result_reason=(
                f"effective_from={moment.isoformat()} routing {before} -> {after}"
            ),
            inputs={
                "effective_from": moment.isoformat(),
                "rules": {
                    cls: {"target": target, "fallback": fallback}
                    for cls, (target, fallback) in sorted(rules.items())
                },
            },
        )
        return await self.read_configured_rules(org_id, moment)
