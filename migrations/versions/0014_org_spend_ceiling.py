"""org_spend_ceiling — org-wide LLM money spend ceiling (mutable config, RLS)

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-28

A NEW FIRST-CLASS OBJECT (owner decision D1). This is the org-level LLM *money*
spend ceiling the platform did not previously have. It is deliberately NOT
``budget_ledger`` (migration 0001) — that is business spend in currency minor
units, department-scoped, and UNFED in production (migration 0011:29-31 records
that nothing in src/ has ever written a budget_ledger row; ADR-0006 forbids
conflating it with LLM cost). This table is read ONLY by the pre-call spend gate
and set ONLY by ops.

Shape (owner decision D2 — kept minimal, nothing else):
  * PRIMARY KEY (org_id, billing_period).
  * ONE value column ``ceiling_micros BIGINT NOT NULL CHECK (>= 0)``.
  * standard created_at / updated_at.
  No soft limits, no per-provider rows, no reserve floors.

Unit (owner decision D3): ``ceiling_micros`` is MICRO-USD — millionths of one
USD — the SAME unit as ``ai_cost_ledger.cost_micros`` (ADR-0006). It is NOT
cents and NOT minor currency units; a units mismatch here would be a 10,000x
error. The unit is asserted in the column comment below and in a test.

Period (owner decision D4): ``billing_period`` is the calendar month as the
``"%Y-%m"`` string already used by ai_cost_ledger.billing_period
(anthropic_adapter.py:382, ``occurred_at.strftime("%Y-%m")``). No second period
concept is introduced.

Mutability + isolation (owner decision D5): this is MUTABLE CONFIG, not
append-only — there is deliberately NO append-only trigger (unlike
ai_cost_ledger / audit_log). It DOES carry RLS tenant isolation modelled EXACTLY
on ai_cost_ledger (migration 0012:178-185): ENABLE + FORCE ROW LEVEL SECURITY so
even the table owner is a policy subject, and a single ``tenant_isolation``
FOR ALL policy on ``current_setting('skylize.org_id')``. The non-superuser
``skylize_app`` role is therefore a genuine RLS subject.

Fail-closed (owner decision D6): a MISSING row for (org_id, current period) means
the call is REFUSED by the gate. There is deliberately NO platform-wide default
ceiling in Settings — an implicit global is unauditable.

Seed (owner decision D7): the table is created EMPTY. The ceiling VALUE is an
owner business decision seeded later by ops; no row is inserted here, not even a
comment suggesting a number.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"


def upgrade() -> None:
    # asyncpg executes only one statement per op.execute() call; each DDL
    # statement is therefore its own call.

    # ------------------------------------------------------------------
    # org_spend_ceiling — one row per (org, calendar month). Minimal by D2.
    # ------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE TABLE org_spend_ceiling (
            org_id         TEXT NOT NULL REFERENCES tenants(org_id),
            billing_period TEXT NOT NULL,                 -- calendar month "%Y-%m" (D4)
            ceiling_micros BIGINT NOT NULL CHECK (ceiling_micros >= 0),
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (org_id, billing_period)
        )
    """))

    # Unit assertion in the schema itself (D3): the number is micro-USD, exactly
    # like ai_cost_ledger.cost_micros — NOT cents, NOT minor currency units.
    op.execute(sa.text(
        "COMMENT ON COLUMN org_spend_ceiling.ceiling_micros IS "
        "'micro-USD: millionths of one USD, the SAME unit as ai_cost_ledger.cost_micros "
        "(ADR-0006). NOT cents, NOT minor currency units.'"
    ))

    # ------------------------------------------------------------------
    # Row-level security — modelled EXACTLY on ai_cost_ledger (migration
    # 0012:178-185): ENABLE + FORCE so even the table owner is subject to the
    # policy, and a single tenant_isolation FOR ALL policy. (D5)
    # ------------------------------------------------------------------
    op.execute(sa.text("ALTER TABLE org_spend_ceiling ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE org_spend_ceiling FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
        CREATE POLICY tenant_isolation ON org_spend_ceiling
        FOR ALL
        USING (org_id = current_setting('skylize.org_id', true))
        WITH CHECK (org_id = current_setting('skylize.org_id', true))
    """))

    # ------------------------------------------------------------------
    # Grants for the non-superuser runtime role (subject to RLS). MUTABLE
    # config (D5), so SELECT + INSERT + UPDATE — enough for the read + upsert
    # setter — but deliberately NOT append-only and with no DELETE path.
    # ------------------------------------------------------------------
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON org_spend_ceiling TO {_APP_ROLE};")

    # ------------------------------------------------------------------
    # Seed: intentionally EMPTY (D7). The ceiling VALUE is an owner business
    # decision, seeded later by ops. A missing (org, period) row FAILS CLOSED
    # (D6). No row — not even a comment suggesting a number — is inserted here.
    # ------------------------------------------------------------------
    # (no INSERTs)


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON org_spend_ceiling FROM {_APP_ROLE};")
    op.execute(sa.text("DROP TABLE IF EXISTS org_spend_ceiling CASCADE"))
