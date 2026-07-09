"""Tests for CapitalDAL."""
from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from skylize.decision_engine.capital_dal import CapitalDAL

from .conftest import make_decision_context


def _make_row(ceiling: int, committed: int) -> dict:
    return {"ceiling": ceiling, "committed": committed}


def _make_total_row(total: int) -> dict:
    return {"total": total}


def _dal(settings, conn_override=None) -> tuple[CapitalDAL, AsyncMock]:
    conn = conn_override or AsyncMock()
    db = MagicMock()

    @asynccontextmanager
    async def _tenant_session(tenant_id: str):
        yield conn

    @asynccontextmanager
    async def _admin_session():
        yield conn

    db.tenant_session = _tenant_session
    db.admin_session = _admin_session
    return CapitalDAL(db, settings), conn


# ---------------------------------------------------------------------------
# check_capital_ceiling passes when available > floor + request
# ---------------------------------------------------------------------------

async def test_capital_ceiling_passes_when_within_budget(settings):
    conn = AsyncMock()
    # available = ceiling - committed = 10000 - 2000 = 8000
    # total org = 20000, reserve_floor = 20000 * 0.15 = 3000
    # spendable = 8000 - 3000 = 5000
    # request=4000 <= 5000 → passes
    conn.fetchrow.side_effect = [
        {"ceiling": 10000, "committed": 2000},   # get_available_budget
        {"total": 20000},                          # _get_total_org_budget
    ]
    dal, _ = _dal(settings, conn)

    result = await dal.check_capital_ceiling("tenant-a", "creative", Decimal("4000"))

    assert result.passes is True
    assert result.available_budget == Decimal("8000")
    assert result.requested_amount == Decimal("4000")


# ---------------------------------------------------------------------------
# Fails when request exceeds spendable
# ---------------------------------------------------------------------------

async def test_capital_ceiling_fails_when_over_spendable(settings):
    conn = AsyncMock()
    # available=8000, total=20000, reserve=3000, spendable=5000
    # request=6000 > 5000 → fails
    conn.fetchrow.side_effect = [
        {"ceiling": 10000, "committed": 2000},
        {"total": 20000},
    ]
    dal, _ = _dal(settings, conn)

    result = await dal.check_capital_ceiling("tenant-a", "creative", Decimal("6000"))

    assert result.passes is False
    assert "SPEND_OVER_CEILING" in result.reason


# ---------------------------------------------------------------------------
# RLS: SET LOCAL app.current_tenant called — tenant_session used for every query
# ---------------------------------------------------------------------------

async def test_rls_tenant_session_used_for_all_queries(settings):
    tenant_sessions: list[str] = []
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"ceiling": 5000, "committed": 0},
        {"total": 5000},
    ]

    db = MagicMock()

    @asynccontextmanager
    async def _tenant_session(tenant_id: str):
        tenant_sessions.append(tenant_id)
        yield conn

    db.tenant_session = _tenant_session

    dal = CapitalDAL(db, settings)
    await dal.check_capital_ceiling("my-tenant", "sales", Decimal("100"))

    # Both queries must run inside tenant_session("my-tenant")
    assert all(t == "my-tenant" for t in tenant_sessions)
    assert len(tenant_sessions) == 2  # get_available_budget + _get_total_org_budget


# ---------------------------------------------------------------------------
# extract_requested_amount: finds amount from all known payload keys
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["requested_amount", "budget_request", "capital_required", "amount"])
async def test_extract_requested_amount_all_keys(settings, key):
    dal, _ = _dal(settings)
    ctx = make_decision_context(payload={key: 999})
    result = await dal.extract_requested_amount(ctx)
    assert result == Decimal("999")


# ---------------------------------------------------------------------------
# extract_requested_amount returns None when absent
# ---------------------------------------------------------------------------

async def test_extract_requested_amount_returns_none_when_absent(settings):
    dal, _ = _dal(settings)
    ctx = make_decision_context(payload={"other_key": "irrelevant"})
    result = await dal.extract_requested_amount(ctx)
    assert result is None


# ---------------------------------------------------------------------------
# get_available_budget raises RuntimeError when no row
# ---------------------------------------------------------------------------

async def test_get_available_budget_raises_when_no_row(settings):
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    dal, _ = _dal(settings, conn)

    with pytest.raises(RuntimeError, match="No budget_ledger row found"):
        await dal.get_available_budget("tenant-x", "creative")


# ---------------------------------------------------------------------------
# ceiling_pct is inf when available_budget is 0
# ---------------------------------------------------------------------------

async def test_ceiling_pct_is_inf_when_available_zero(settings):
    conn = AsyncMock()
    # available = 0 - 0 = 0
    conn.fetchrow.side_effect = [
        {"ceiling": 0, "committed": 0},
        {"total": 1000},
    ]
    dal, _ = _dal(settings, conn)

    result = await dal.check_capital_ceiling("tenant-a", "creative", Decimal("100"))

    import math
    assert math.isinf(result.ceiling_pct)
    assert result.passes is False


# ---------------------------------------------------------------------------
# Priority order: requested_amount wins over budget_request
# ---------------------------------------------------------------------------

async def test_extract_priority_order(settings):
    dal, _ = _dal(settings)
    ctx = make_decision_context(payload={"requested_amount": 111, "budget_request": 222})
    result = await dal.extract_requested_amount(ctx)
    assert result == Decimal("111")  # first key wins
