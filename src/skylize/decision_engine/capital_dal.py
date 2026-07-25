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
    import asyncpg

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
            return await self._total_org_budget_on_conn(conn)

    @staticmethod
    async def _total_org_budget_on_conn(conn: "asyncpg.Connection") -> Decimal:
        """SUM(ceiling) over the most-recent-period department scopes, on an EXISTING
        tenant-bound connection.  Returns 0 when no rows exist.  Shared by the stage-4
        ceiling read and the transactional reservation so both derive the reserve
        floor from the identical figure (ceilings are human-set and stable across a
        decision, so this needs no row lock)."""
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

    def _reserve_floor(self, total_org_budget: Decimal) -> Decimal:
        """reserve_floor = total_org_budget × capital_reserve_floor_pct, quantized to
        whole minor units.  The single definition of the floor, applied identically by
        the stage-4 ceiling check and the reservation guard so the two can never
        disagree on the spendable boundary — only on concurrency, which the
        reservation's row lock resolves."""
        return (
            total_org_budget * Decimal(str(self._settings.capital_reserve_floor_pct))
        ).quantize(Decimal("1"))

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

        reserve_floor = self._reserve_floor(total_budget)
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

    # -- write path: transactional reservation (capital_allocation.md §4) -------

    async def reserve_committed(
        self, conn: "asyncpg.Connection", department: str, amount: Decimal
    ) -> bool:
        """Atomically reserve *amount* (minor units) against ``committed`` for the
        department's most-recent-period ledger row, ON AN EXISTING tenant-bound
        transaction (*conn* from ``Database.tenant_session``).

        The row is taken with ``SELECT ... FOR UPDATE``, so concurrent reservations
        against the same scope SERIALIZE on the row lock and re-read ``committed``
        after the prior holder commits — no two proposals can jointly overshoot the
        ceiling.  Returns True and increments ``committed`` when the reservation fits
        within ``spendable = (ceiling − committed) − reserve_floor`` (the identical
        boundary the stage-4 check uses); returns False and writes nothing when it
        would breach, or when no ledger row exists for the scope (FAIL CLOSED —
        capital_allocation.md §7).  Never raises on the breach path; the caller
        converts a False into a deferral (SPEND_OVER_CEILING).

        Money is Decimal throughout; the BIGINT column takes ``int(amount)`` (minor
        units are integral by contract).  RLS: *conn* is already bound to the tenant
        via ``SET LOCAL skylize.org_id``, so this can only touch that tenant's rows.
        """
        scope = f"department:{department}"
        row = await conn.fetchrow(
            """
            SELECT ledger_id, ceiling, committed
            FROM budget_ledger
            WHERE scope = $1
            ORDER BY period DESC
            LIMIT 1
            FOR UPDATE
            """,
            scope,
        )
        if row is None:
            return False  # no ceiling configured for scope → fail closed → defer

        total_org_budget = await self._total_org_budget_on_conn(conn)
        reserve_floor = self._reserve_floor(total_org_budget)
        available = Decimal(row["ceiling"]) - Decimal(row["committed"])
        spendable = available - reserve_floor
        if amount > spendable:
            return False

        await conn.execute(
            """
            UPDATE budget_ledger
            SET committed = committed + $1, updated_at = now()
            WHERE ledger_id = $2
            """,
            int(amount),
            row["ledger_id"],
        )
        return True

    async def release_committed(
        self, conn: "asyncpg.Connection", department: str, amount: Decimal
    ) -> Decimal:
        """Reverse a reservation: decrement ``committed`` by *amount* (minor units) on
        the department's most-recent-period ledger row, floored at ``spent`` so the
        ``spent <= committed`` invariant (migration 0001 CHECK) always holds.  Returns
        the new ``committed`` as Decimal; a no-op (returns 0) when no ledger row
        exists.  Row-locked, tenant-bound like ``reserve_committed``.

        This is the reversal primitive a settlement / compensation consumer must call
        when an approved spend's execution fails, so ``committed`` is not a ratchet.
        NO caller wires it yet — that consumer is out of scope for this change and is
        tracked in DECISIONS_PENDING.md; the primitive exists (and is tested) so the
        reservation is reversible the moment that consumer lands.
        """
        scope = f"department:{department}"
        row = await conn.fetchrow(
            """
            SELECT ledger_id, committed, spent
            FROM budget_ledger
            WHERE scope = $1
            ORDER BY period DESC
            LIMIT 1
            FOR UPDATE
            """,
            scope,
        )
        if row is None:
            return Decimal(0)

        new_committed = max(int(row["committed"]) - int(amount), int(row["spent"]))
        await conn.execute(
            """
            UPDATE budget_ledger
            SET committed = $1, updated_at = now()
            WHERE ledger_id = $2
            """,
            new_committed,
            row["ledger_id"],
        )
        return Decimal(new_committed)
