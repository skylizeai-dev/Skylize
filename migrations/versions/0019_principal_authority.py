"""principal authority, spend ledger, and work journal

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-01

The per-employee authority kernel (skylize.app.principal): a human principal's
compiled authority, an atomic spend-reservation ledger, and an append-only work
journal shared by the autonomous and co-work agent shapes. NOT YET WIRED into
`GovernanceAuthority.mint()` or the tool proxy — this migration only lands the
durable storage; the application code (skylize/app/principal/*.py) is pure
Python plus one asyncpg adapter that is not constructed anywhere yet.

CORRECTIONS FROM THE ORIGINAL DRAFT (PROMPT 0 audit, treated as ground truth):
  * Converted from a raw .sql file to this repo's actual migration convention
    (Alembic, sequential zero-padded revisions; 0018 was HEAD).
  * RLS GUC fixed to `skylize.org_id` (audit A9) — the draft used `app.org_id`,
    which nothing in this codebase sets; every existing policy uses
    `skylize.org_id` (e.g. 0007_org_credentials.py:66-67, dal/connection.py:79).
  * Policy name fixed to `tenant_isolation` per table (the fixed name used by
    every existing migration), not `<table>_org_isolation`.
  * Every `org_id` column now carries `REFERENCES tenants(org_id)`, matching the
    convention in all ten prior migrations that add an org_id column.
  * Explicit per-table `GRANT ... TO skylize_app` added for all six tables
    (missing entirely from the draft) — `work_journal` is restricted to
    SELECT, INSERT, mirroring the audit_log append-only grant in
    0003_app_role_rls_subject.py:76-77 (least privilege on top of the trigger).
  * The RLS block is a single `DO $$ ... FOREACH ... $$` statement — this is
    precedented for multi-table migrations in 0001_initial_schema.py:347-367
    ("The DO block is a single statement — one op.execute() is correct here.")
  * work_journal's append-only trigger function is its own
    `work_journal_prevent_mutation()`, not the existing `skylize_prevent_mutation()`
    (migration 0001) — that function's exception message hardcodes "audit_log"
    and must not be reused verbatim for a different table.

asyncpg executes only one statement per op.execute() call; every DDL statement
below is therefore its own call (see 0011_decision_engine_stores.py, 0014, etc.).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"

_TENANT_TABLES = [
    "principal",
    "principal_grant",
    "spend_envelope",
    "spend_reservation",
    "work_journal",
    "journal_cursor",
]


def upgrade() -> None:
    # asyncpg executes only one statement per op.execute() call; every DDL
    # statement must be its own call.

    # ------------------------------------------------------------------
    # Principals and grants
    # ------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE TABLE principal (
            principal_id            TEXT        NOT NULL,
            org_id                  TEXT        NOT NULL REFERENCES tenants(org_id),
            display_name            TEXT        NOT NULL,
            position_id             TEXT,
            authority_level         TEXT        NOT NULL
                CHECK (authority_level IN ('executive','vp','director','manager','worker')),
            manager_principal_id    TEXT,
            suspended_at            TIMESTAMPTZ,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (org_id, principal_id)
        )
    """))

    op.execute(sa.text("""
        CREATE TABLE principal_grant (
            grant_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id          TEXT        NOT NULL REFERENCES tenants(org_id),
            principal_id    TEXT        NOT NULL,
            scope           TEXT        NOT NULL,
            source          TEXT        NOT NULL
                CHECK (source IN ('position','group','explicit_grant','explicit_deny')),
            justification   TEXT,
            valid_from      TIMESTAMPTZ NOT NULL DEFAULT now(),
            valid_to        TIMESTAMPTZ,
            created_by      TEXT        NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            FOREIGN KEY (org_id, principal_id) REFERENCES principal (org_id, principal_id),
            CHECK (valid_to IS NULL OR valid_to > valid_from),
            CHECK (source NOT IN ('explicit_grant','explicit_deny')
                   OR (justification IS NOT NULL AND length(btrim(justification)) > 0))
        )
    """))

    op.execute(sa.text(
        "CREATE INDEX principal_grant_lookup "
        "ON principal_grant (org_id, principal_id, valid_from DESC)"
    ))

    # ------------------------------------------------------------------
    # Spend
    # ------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE TABLE spend_envelope (
            envelope_id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id                  TEXT        NOT NULL REFERENCES tenants(org_id),
            principal_id            TEXT        NOT NULL,
            currency                CHAR(3)     NOT NULL DEFAULT 'USD',
            ceiling_minor           BIGINT      NOT NULL CHECK (ceiling_minor >= 0),
            reserved_minor          BIGINT      NOT NULL DEFAULT 0 CHECK (reserved_minor >= 0),
            spent_minor             BIGINT      NOT NULL DEFAULT 0 CHECK (spent_minor >= 0),
            period_start            TIMESTAMPTZ NOT NULL,
            period_end              TIMESTAMPTZ NOT NULL,
            over_ceiling_behavior   TEXT        NOT NULL
                CHECK (over_ceiling_behavior IN ('hard_deny','defer_to_human')),
            revoked_at              TIMESTAMPTZ,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (period_end > period_start),
            CHECK (spent_minor + reserved_minor <= ceiling_minor)
        )
    """))

    # Needed for the EXCLUDE constraint below (GiST support for = on text).
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    # One active envelope per principal per instant — without this,
    # overlapping periods would silently double a person's budget.
    op.execute(sa.text("""
        ALTER TABLE spend_envelope ADD CONSTRAINT spend_envelope_no_overlap
            EXCLUDE USING gist (
                org_id WITH =,
                principal_id WITH =,
                tstzrange(period_start, period_end) WITH &&
            ) WHERE (revoked_at IS NULL)
    """))

    op.execute(sa.text("""
        CREATE TABLE spend_reservation (
            reservation_id      UUID        PRIMARY KEY,
            envelope_id         UUID        NOT NULL REFERENCES spend_envelope (envelope_id),
            org_id              TEXT        NOT NULL REFERENCES tenants(org_id),
            idempotency_key     TEXT        NOT NULL,
            amount_minor        BIGINT      NOT NULL CHECK (amount_minor > 0),
            committed_minor     BIGINT      CHECK (committed_minor IS NULL OR committed_minor >= 0),
            correlation_id      UUID        NOT NULL,
            governance_token_id UUID,
            state               TEXT        NOT NULL
                CHECK (state IN ('held','committed','released','expired')),
            created_at          TIMESTAMPTZ NOT NULL,
            expires_at          TIMESTAMPTZ NOT NULL,
            settled_at          TIMESTAMPTZ,
            UNIQUE (org_id, idempotency_key),
            CHECK (committed_minor IS NULL OR committed_minor <= amount_minor)
        )
    """))

    op.execute(sa.text(
        "CREATE INDEX spend_reservation_sweep "
        "ON spend_reservation (expires_at) WHERE state = 'held'"
    ))

    # ------------------------------------------------------------------
    # Work journal
    # ------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE TABLE work_journal (
            seq                 BIGSERIAL   PRIMARY KEY,
            org_id              TEXT        NOT NULL REFERENCES tenants(org_id),
            principal_id        TEXT        NOT NULL,
            actor_kind          TEXT        NOT NULL
                CHECK (actor_kind IN ('human','agent_autonomous','agent_cowork')),
            actor_id            TEXT        NOT NULL,
            correlation_id      UUID        NOT NULL,
            governance_token_id UUID,
            kind                TEXT        NOT NULL,
            headline            TEXT        NOT NULL CHECK (length(headline) BETWEEN 1 AND 280),
            detail              JSONB       NOT NULL DEFAULT '{}'::jsonb,
            cost_minor          BIGINT      NOT NULL DEFAULT 0 CHECK (cost_minor >= 0),
            requires_attention  BOOLEAN     NOT NULL DEFAULT false,
            occurred_at         TIMESTAMPTZ NOT NULL
        )
    """))

    op.execute(sa.text(
        "CREATE INDEX work_journal_read "
        "ON work_journal (org_id, principal_id, seq)"
    ))
    op.execute(sa.text(
        "CREATE INDEX work_journal_attention "
        "ON work_journal (org_id, principal_id, seq) WHERE requires_attention"
    ))

    # Append-only. Same posture as audit_log (0001_initial_schema.py:372-385),
    # own function/trigger so the exception message names the right table.
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION work_journal_prevent_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'work_journal is append-only: % is not permitted', TG_OP;
        END;
        $$ LANGUAGE plpgsql
    """))

    op.execute(sa.text("""
        CREATE TRIGGER work_journal_append_only
            BEFORE UPDATE OR DELETE ON work_journal
            FOR EACH ROW EXECUTE FUNCTION work_journal_prevent_mutation()
    """))

    op.execute(sa.text("""
        CREATE TABLE journal_cursor (
            org_id          TEXT        NOT NULL REFERENCES tenants(org_id),
            principal_id    TEXT        NOT NULL,
            last_seen_seq   BIGINT      NOT NULL DEFAULT 0 CHECK (last_seen_seq >= 0),
            last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (org_id, principal_id)
        )
    """))

    # ------------------------------------------------------------------
    # Row-level security — ENABLE + FORCE on every table (matches
    # org_credentials / audit_log posture). The DO block is a single
    # statement — one op.execute() is correct here (precedent:
    # 0001_initial_schema.py:347-367).
    # ------------------------------------------------------------------
    op.execute(sa.text("""
        DO $$
        DECLARE t TEXT;
        BEGIN
            FOREACH t IN ARRAY ARRAY[
                'principal','principal_grant','spend_envelope','spend_reservation',
                'work_journal','journal_cursor'
            ]
            LOOP
                EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', t);
                EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', t);
                EXECUTE format($f$
                    CREATE POLICY tenant_isolation ON %I
                    FOR ALL
                    USING (org_id = current_setting('skylize.org_id', true))
                    WITH CHECK (org_id = current_setting('skylize.org_id', true));
                $f$, t);
            END LOOP;
        END $$
    """))

    # ------------------------------------------------------------------
    # Grants for the non-superuser runtime role. work_journal is
    # append-only (trigger above): SELECT, INSERT only, matching the
    # audit_log precedent (0003_app_role_rls_subject.py:76-77).
    # ------------------------------------------------------------------
    for table in _TENANT_TABLES:
        if table == "work_journal":
            op.execute(f"GRANT SELECT, INSERT ON {table} TO {_APP_ROLE};")
        else:
            op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {_APP_ROLE};")


def downgrade() -> None:
    for table in _TENANT_TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM {_APP_ROLE};")

    op.execute(sa.text("DROP TRIGGER IF EXISTS work_journal_append_only ON work_journal"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS work_journal_prevent_mutation()"))

    for table in [
        "journal_cursor",
        "work_journal",
        "spend_reservation",
        "spend_envelope",
        "principal_grant",
        "principal",
    ]:
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table} CASCADE"))
