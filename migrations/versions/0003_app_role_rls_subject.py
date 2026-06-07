"""dedicated non-superuser app role that is SUBJECT to RLS

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-02

ARCHITECTURAL FIX (Sprint-2). The Sprint-1 schema enabled FORCE ROW LEVEL
SECURITY and claimed "isolation cannot be bypassed by connecting as a privileged
role." That is FALSE if the application connects as a Postgres SUPERUSER or a
role with BYPASSRLS — such roles bypass RLS unconditionally, even with FORCE.
The compose deployment connected the app as the bootstrap superuser, so RLS was
enforcing nothing in practice.

This migration creates `skylize_app`: NOSUPERUSER, NOBYPASSRLS, NOCREATEDB,
NOCREATEROLE. It is granted exactly the DML it needs and is therefore SUBJECT to
the `tenant_isolation` RLS policy. The application (bootstrap.py) connects as
this role; migrations and extension creation continue to run as the superuser.

Idempotent: safe to re-run (role/grants guarded). The role password is taken from
`SKYLIZE_APP_DB_PASSWORD` at migration time; if unset, a login role without a
password is created (local/dev only — production MUST set it).
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = [
    "governance_tokens", "agent_live_state", "kill_switch_state",
    "budget_ledger", "decisions", "hitl_queue", "memory_records",
    "kg_nodes", "kg_edges", "audit_log", "tenant_integrations",
]

# Platform tables the app also reads/writes (no RLS, but still needs grants).
_PLATFORM_TABLES = ["tenants", "tenant_users", "agent_contracts"]

_APP_ROLE = "skylize_app"


def upgrade() -> None:
    password = os.environ.get("SKYLIZE_APP_DB_PASSWORD", "")
    pw_clause = f"LOGIN PASSWORD '{password}'" if password else "LOGIN"

    # Create the role if absent. Explicitly NOSUPERUSER NOBYPASSRLS so it is
    # subject to RLS — the whole point of this migration.
    op.execute(
        f"""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
            CREATE ROLE {_APP_ROLE} {pw_clause}
                NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
        ELSE
            ALTER ROLE {_APP_ROLE} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
        END IF;
    END $$;
    """
    )

    # Schema usage + DML on every table the app touches. Reads/writes on tenant
    # tables remain filtered by RLS because the role is NOBYPASSRLS.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE};")

    all_tables = _TENANT_TABLES + _PLATFORM_TABLES
    for table in all_tables:
        # audit_log is append-only: app may INSERT/SELECT but not UPDATE/DELETE
        # (the trigger also blocks it, but least-privilege belt-and-suspenders).
        if table == "audit_log":
            op.execute(f"GRANT SELECT, INSERT ON {table} TO {_APP_ROLE};")
        else:
            op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {_APP_ROLE};")

    # Sequences (gen_random_uuid defaults need no sequence, but be safe for any).
    op.execute(
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE};"
    )

    # Future-proof default privileges so later tables/sequences inherit grants.
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_APP_ROLE};"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {_APP_ROLE};"
    )


def downgrade() -> None:
    all_tables = _TENANT_TABLES + _PLATFORM_TABLES
    for table in all_tables:
        op.execute(f"REVOKE ALL ON {table} FROM {_APP_ROLE};")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {_APP_ROLE};")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {_APP_ROLE};")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {_APP_ROLE};"
    )
    # The role itself is left in place (other DBs/objects may depend on it);
    # dropping a role with dependent grants would fail. Explicit DROP is an ops
    # decision, not an automatic downgrade.
