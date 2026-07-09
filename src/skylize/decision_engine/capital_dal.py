"""Capital DAL — budget ledger queries for the Decision Engine.

RLS DEPENDENCY: Every query runs inside ``database.tenant_session(tenant_id)``,
which executes ``SET LOCAL skylize.org_id = <tenant_id>`` before the query.
The Postgres RLS policy on ``budget_ledger`` filters to that org_id.
This module never bypasses that contract and never formats tenant_id into SQL.

The session variable name is ``skylize.org_id`` (see dal/connection.py and
migration 0001). The runtime role must be ``skylize_app`` (non-superuser) so
FORCE ROW LEVEL SECURITY applies (see migration 0003 and
docs/architecture/05_security_architecture.md §8).

``budget_ledger.ceiling`` and related columns are BIGINT (currency minor units,
e.g. cents). All public methods return ``Decimal`` — never float — to avoid
rounding errors on money arithmetic.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from skylize.dal.connection import Database

from skylize.decision_engine.config import DecisionEngineSettings
from skylize.decision_engine.models import CapitalCheckResult, DecisionContext

# Payload keys checked in order when extracting a requested spend amount.
_AMOUNT_KEYS = ("requested_amount", "budget_request", "capital_required", "amount")


class CapitalDAL:
    def __init__(self, db: "Database", settings: DecisionEngineSettings) -> None:
        self._db = db
        self._settings = settings

    async def get_available_budget(self, tenant_id: str, department: str) -> Decimal:
        """Return available budget (ceiling − committed) for *department* in minor units.

        Queries within a tenant_session so RLS isolates to tenant_id.
        Raises RuntimeError if no matching ledger row exists.
        """
        scope = f"department:{department}"
        async with self._db.tenant_session(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT ceiling, committed
                FROM budget_ledger
                WHERE scope = $1
                ORDER BY period DESC
                LIMIT 1
                """,
                scope,
            )
        if row is None:
            raise RuntimeError(
                f"No budget_ledger row found for tenant={tenant_id!r} scope={scope!r}"
            )
        available = Decimal(row["ceiling"]) - Decimal(row["committed"])
        return available

    async def _get_total_org_budget(self, tenant_id: str) -> Decimal:
        """Sum ceiling across all department scopes for the tenant (most recent period).

        Used only for reserve floor calculation; returns 0 if no rows exist.
        """
        async with self._db.tenant_session(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT COALESCE(SUM(ceiling), 0) AS total
                FROM budget_ledger
                WHERE scope LIKE 'department:%'
                  AND period = (
                      SELECT MAX(period)
                      FROM budget_ledger
                      WHERE scope LIKE 'department:%'
                  )
                """,
            )
        return Decimal(row["total"]) if row else Decimal(0)

    async def check_capital_ceiling(
        self,
        tenant_id: str,
        department: str,
        requested_amount: Decimal,
    ) -> CapitalCheckResult:
        """Check whether *requested_amount* fits within spendable budget.

        spendable = available_budget − reserve_floor
        reserve_floor = total_org_budget × capital_reserve_floor_pct

        Returns a CapitalCheckResult; raises nothing — callers decide whether
        to raise CapitalCeilingExceeded.
        """
        available_budget = await self.get_available_budget(tenant_id, department)
        total_budget = await self._get_total_org_budget(tenant_id)

        reserve_floor = (
            total_budget * Decimal(str(self._settings.capital_reserve_floor_pct))
        ).quantize(Decimal("1"))
        spendable = available_budget - reserve_floor

        ceiling_pct = (
            float(requested_amount / available_budget * 100)
            if available_budget > 0
            else float("inf")
        )

        passes = requested_amount <= spendable

        if passes:
            reason = (
                f"Request {requested_amount} within spendable {spendable} "
                f"(available {available_budget} − reserve_floor {reserve_floor})"
            )
        else:
            deficit = requested_amount - spendable
            reason = (
                f"SPEND_OVER_CEILING: requested {requested_amount} exceeds spendable "
                f"{spendable} by {deficit} "
                f"(available {available_budget}, reserve_floor {reserve_floor})"
            )

        return CapitalCheckResult(
            available_budget=available_budget,
            requested_amount=requested_amount,
            ceiling_pct=ceiling_pct,
            passes=passes,
            reason=reason,
        )

    async def extract_requested_amount(
        self, context: DecisionContext
    ) -> Decimal | None:
        """Scan ``context.payload`` for a financial ask and return it as Decimal.

        Checks keys in order: ``requested_amount``, ``budget_request``,
        ``capital_required``, ``amount``.  Returns ``None`` when none are present,
        which callers treat as an auto-pass with reason "no capital request in payload".
        """
        for key in _AMOUNT_KEYS:
            value = context.payload.get(key)
            if value is not None:
                return Decimal(str(value))
        return None
