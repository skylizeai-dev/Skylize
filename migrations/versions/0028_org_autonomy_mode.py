"""org_autonomy_mode — the ORG-WIDE autonomy posture (mutable config, RLS)

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-12

Scope (owner ruling 6): the autonomy setting is ORG-WIDE. There is deliberately
no per-department, per-agent or per-user row. One org has one posture at a time;
anything narrower would let a quiet corner of the org run hotter than the owner
believes, which is the exact failure this table exists to prevent.

Shape: PRIMARY KEY (org_id, effective_from), one value column ``autonomy_mode``
constrained by a CHECK to the five named modes. The mode strings are the SAME
five that ``skylize.contracts.base.AutonomyMode`` declares and the console
persists; the CHECK is what stops a sixth value entering through SQL.

``effective_from`` makes the setting effective-dated, the same shape migration
0014 gives ``org_spend_ceiling``: the mode IN FORCE is the row with the greatest
``effective_from`` at or before now, so a mode set once stays in force until a
newer row supersedes it, and history is kept rather than overwritten. Unlike
0014 the period is a TIMESTAMPTZ, not a ``"%Y-%m"`` month: an autonomy change
takes effect at an instant, not at a billing boundary.

Fail-closed (owner ruling 7, no exceptions): a MISSING row resolves to
``observe`` — every action requires a human. That default is NOT a column
DEFAULT and NOT a seeded row; it lives in the read path
(``dal/org_autonomy_mode.py``) precisely so that "nobody has set this" and
"somebody chose observe" stay distinguishable in the table while resolving to
the same safe behaviour. An implicit platform-wide default row would be
unauditable, the same reasoning migration 0014 gives at D7.

Mutability + isolation: MUTABLE CONFIG, so no append-only trigger. RLS is
modelled EXACTLY on org_spend_ceiling (migration 0014) and through it on
ai_cost_ledger (migration 0012:178-185): ENABLE + FORCE ROW LEVEL SECURITY so
even the table owner is a policy subject, and a single ``tenant_isolation``
FOR ALL policy on ``current_setting('skylize.org_id')``. The non-superuser
``skylize_app`` role is therefore a genuine RLS subject: one org can neither
read nor write another org's posture.

Seed: the table is created EMPTY. Every org starts at ``observe`` by the
fail-closed read, which is the correct posture for an org whose owner has not
yet chosen one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"


def upgrade() -> None:
    # asyncpg executes only one statement per op.execute() call; each DDL
    # statement is therefore its own call.

    # ------------------------------------------------------------------
    # org_autonomy_mode — one row per (org, effective instant). Org-wide only.
    # ------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE TABLE org_autonomy_mode (
            org_id         TEXT NOT NULL REFERENCES tenants(org_id),
            effective_from TIMESTAMPTZ NOT NULL DEFAULT now(),
            autonomy_mode  TEXT NOT NULL CHECK (autonomy_mode IN ('observe', 'propose', 'act_within_budget', 'act_and_reallocate', 'act_governed')),
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (org_id, effective_from)
        )
    """))

    op.execute(sa.text(
        "COMMENT ON COLUMN org_autonomy_mode.autonomy_mode IS "
        "'One of the five modes in skylize.contracts.base.AutonomyMode, least to "
        "most autonomous. A MISSING row resolves to observe (fail closed); that "
        "default lives in the read path, not in a column DEFAULT, so an unset org "
        "stays distinguishable from one that chose observe.'"
    ))

    # ------------------------------------------------------------------
    # Row-level security — modelled EXACTLY on org_spend_ceiling (migration
    # 0014) and through it on ai_cost_ledger (migration 0012:178-185).
    # ------------------------------------------------------------------
    op.execute(sa.text("ALTER TABLE org_autonomy_mode ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE org_autonomy_mode FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
        CREATE POLICY tenant_isolation ON org_autonomy_mode
        FOR ALL
        USING (org_id = current_setting('skylize.org_id', true))
        WITH CHECK (org_id = current_setting('skylize.org_id', true))
    """))

    # ------------------------------------------------------------------
    # Grants for the non-superuser runtime role (subject to RLS). Mutable
    # config, so SELECT + INSERT + UPDATE for the read + upsert setter.
    #
    # The REVOKE is not redundant. Migration 0003 set ALTER DEFAULT PRIVILEGES
    # granting skylize_app `arwd` on every table created afterwards, so a new
    # table arrives with DELETE already granted and the GRANT above is a subset
    # of what the role holds. Without this REVOKE the app role could erase an
    # org's posture history, which is audit evidence of who let the agents run
    # how far. ai_cost_ledger solves the same problem with an append-only
    # trigger; this table is mutable config, so a grant is the right instrument.
    # ------------------------------------------------------------------
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON org_autonomy_mode TO {_APP_ROLE};")
    op.execute(f"REVOKE DELETE ON org_autonomy_mode FROM {_APP_ROLE};")

    # ------------------------------------------------------------------
    # Seed: intentionally EMPTY. An org with no row reads as `observe`.
    # ------------------------------------------------------------------
    # (no INSERTs)


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON org_autonomy_mode FROM {_APP_ROLE};")
    op.execute(sa.text("DROP TABLE IF EXISTS org_autonomy_mode CASCADE"))
