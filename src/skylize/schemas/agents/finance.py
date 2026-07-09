"""Finance agent I/O models (MVP: `cfo_agent`)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FinancialReviewIn(_Base):
    request_id: str
    line_items: list[dict[str, object]]
    currency: str = "USD"


class FinancialDecisionOut(_Base):
    request_id: str
    approved: bool
    notes: str


# ── CFO Agent — budget_summary capability ────────────────────────────────────
class BudgetLineItem(_Base):
    category: str
    amount: float


class BudgetSummaryExecuteIn(_Base):
    department: str
    period: str
    line_items: list[BudgetLineItem]


class BudgetSummaryExecuteOut(_Base):
    summary: str
    total: float
    flags: list[str]
    recommendation: str
