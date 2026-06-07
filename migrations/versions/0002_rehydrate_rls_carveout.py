"""rehydrate RLS read carve-out for governance snapshot warm-up

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-01

The Governance Authority warms its in-memory snapshot (active kills, revoked
tokens, suspended agents) across ALL tenants at startup (Sprint-2 Task 2). RLS
with FORCE blocks even the owner, so the rehydration reads need a carve-out.

This rewrites the `tenant_isolation` policy on each tenant table so that:
  - READ (USING) passes when the row's org_id matches the tenant binding OR a
    read-only `skylize.rehydrate = 'on'` flag is set (startup snapshot warm-up);
  - WRITE (WITH CHECK) is UNCHANGED — still requires a matching org_id, so the
    rehydrate flag can never be used to write across tenants.

Tenant isolation for the request path is therefore preserved exactly; only a
startup, read-only, platform-wide warm-up is newly permitted.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RLS_TABLES = [
    "governance_tokens", "agent_live_state", "kill_switch_state",
    "budget_ledger", "decisions", "hitl_queue", "memory_records",
    "kg_nodes", "kg_edges", "audit_log", "tenant_integrations",
]


def upgrade() -> None:
    op.execute(
        """
    DO $$
    DECLARE t TEXT;
    BEGIN
        FOREACH t IN ARRAY ARRAY[
            'governance_tokens','agent_live_state','kill_switch_state',
            'budget_ledger','decisions','hitl_queue','memory_records',
            'kg_nodes','kg_edges','audit_log','tenant_integrations'
        ]
        LOOP
            EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I;', t);
            EXECUTE format($f$
                CREATE POLICY tenant_isolation ON %I
                FOR ALL
                USING (
                    org_id = current_setting('skylize.org_id', true)
                    OR current_setting('skylize.rehydrate', true) = 'on'
                )
                WITH CHECK (org_id = current_setting('skylize.org_id', true));
            $f$, t);
        END LOOP;
    END $$;
    """
    )


def downgrade() -> None:
    op.execute(
        """
    DO $$
    DECLARE t TEXT;
    BEGIN
        FOREACH t IN ARRAY ARRAY[
            'governance_tokens','agent_live_state','kill_switch_state',
            'budget_ledger','decisions','hitl_queue','memory_records',
            'kg_nodes','kg_edges','audit_log','tenant_integrations'
        ]
        LOOP
            EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I;', t);
            EXECUTE format($f$
                CREATE POLICY tenant_isolation ON %I
                FOR ALL
                USING (org_id = current_setting('skylize.org_id', true))
                WITH CHECK (org_id = current_setting('skylize.org_id', true));
            $f$, t);
        END LOOP;
    END $$;
    """
    )
