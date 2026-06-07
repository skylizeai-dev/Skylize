"""initial schema — tenants, contracts, governance, decisions, memory, audit

Revision ID: 0001
Revises:
Create Date: 2026-06-01

Foundation schema for Sprint 1. Tables, indexes, row-level security, and the
append-only audit trigger. Tenant tables use FORCE ROW LEVEL SECURITY so the
isolation policy applies even to the table *owner*. Every tenant query must
`SET LOCAL skylize.org_id = <org_id>` first (see dal/connection.py).

CORRECTION (Sprint-2, migration 0003): FORCE RLS does NOT stop a SUPERUSER or
BYPASSRLS role — such roles bypass RLS unconditionally. Isolation therefore
depends on the runtime connecting as the non-superuser `skylize_app` role
created in 0003, NOT as the bootstrap superuser. See 0003 and
docs/architecture/05_security_architecture.md §8.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tenant-scoped tables that get RLS (org_id isolation).
_RLS_TABLES = [
    "governance_tokens",
    "agent_live_state",
    "kill_switch_state",
    "budget_ledger",
    "decisions",
    "hitl_queue",
    "memory_records",
    "kg_nodes",
    "kg_edges",
    "audit_log",
    "tenant_integrations",
]


def upgrade() -> None:
    op.execute(
        """
    -- ========================================================
    -- TENANT / IDENTITY (no RLS: cross-tenant visibility needed at auth layer)
    -- ========================================================
    CREATE TABLE tenants (
        org_id        TEXT PRIMARY KEY,
        display_name  TEXT NOT NULL,
        oidc_issuer   TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active','suspended','killed')),
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE TABLE tenant_users (
        user_id    TEXT NOT NULL,
        org_id     TEXT NOT NULL REFERENCES tenants(org_id),
        role       TEXT NOT NULL
                   CHECK (role IN ('owner','admin','operator','analyst','viewer')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (user_id, org_id)
    );

    -- ========================================================
    -- AGENT CONTRACT REGISTRY (platform-level; no RLS)
    -- ========================================================
    CREATE TABLE agent_contracts (
        agent_id      TEXT NOT NULL,
        version       INTEGER NOT NULL DEFAULT 1,
        contract_json JSONB NOT NULL,
        is_active     BOOLEAN NOT NULL DEFAULT true,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (agent_id, version)
    );
    CREATE INDEX idx_agent_contracts_active
        ON agent_contracts (agent_id) WHERE is_active = true;

    -- ========================================================
    -- GOVERNANCE
    -- ========================================================
    CREATE TABLE governance_tokens (
        token_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        agent_id        TEXT NOT NULL,
        org_id          TEXT NOT NULL REFERENCES tenants(org_id),
        authority_level TEXT NOT NULL
                        CHECK (authority_level IN
                               ('executive','vp','director','manager','worker')),
        department      TEXT NOT NULL,
        scope           TEXT[] NOT NULL,
        max_token_budget           INTEGER NOT NULL,
        max_execution_time_seconds INTEGER NOT NULL,
        issued_at       TIMESTAMPTZ NOT NULL,
        expires_at      TIMESTAMPTZ NOT NULL,
        revoked_at      TIMESTAMPTZ,
        revocation_reason TEXT,
        correlation_id  UUID NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX idx_governance_tokens_org ON governance_tokens (org_id);
    CREATE INDEX idx_governance_tokens_agent ON governance_tokens (agent_id, org_id);
    CREATE INDEX idx_governance_tokens_active ON governance_tokens (token_id)
        WHERE revoked_at IS NULL;

    CREATE TABLE agent_live_state (
        agent_id  TEXT NOT NULL,
        org_id    TEXT NOT NULL,
        state     TEXT NOT NULL DEFAULT 'active'
                  CHECK (state IN ('active','suspended','killed')),
        reason    TEXT,
        circuit_breaker_trips INTEGER NOT NULL DEFAULT 0,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (agent_id, org_id)
    );

    CREATE TABLE kill_switch_state (
        scope_type  TEXT NOT NULL
                    CHECK (scope_type IN ('agent','department','tenant','platform')),
        scope_id    TEXT NOT NULL,
        org_id      TEXT NOT NULL,
        engaged_at    TIMESTAMPTZ,
        engaged_by    TEXT,
        reason        TEXT,
        disengaged_at TIMESTAMPTZ,
        PRIMARY KEY (scope_type, scope_id, org_id)
    );

    -- ========================================================
    -- BUDGET LEDGER
    -- ========================================================
    CREATE TABLE budget_ledger (
        ledger_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id     TEXT NOT NULL REFERENCES tenants(org_id),
        scope      TEXT NOT NULL,
        ceiling    BIGINT NOT NULL,
        committed  BIGINT NOT NULL DEFAULT 0,
        spent      BIGINT NOT NULL DEFAULT 0,
        currency   TEXT NOT NULL DEFAULT 'USD',
        period     TEXT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT committed_lte_ceiling CHECK (committed <= ceiling),
        CONSTRAINT spent_lte_committed   CHECK (spent <= committed)
    );
    CREATE INDEX idx_budget_ledger_org_scope
        ON budget_ledger (org_id, scope, period);

    -- ========================================================
    -- DECISIONS + HITL
    -- ========================================================
    CREATE TABLE decisions (
        decision_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id          TEXT NOT NULL REFERENCES tenants(org_id),
        correlation_id  UUID NOT NULL,
        causation_event_id UUID,
        partition_key   TEXT NOT NULL,
        proposing_agent TEXT NOT NULL,
        authority_level TEXT NOT NULL,
        action_kind     TEXT NOT NULL,
        proposal_json   JSONB NOT NULL,
        outcome         TEXT NOT NULL
                        CHECK (outcome IN
                          ('approved','rejected','deferred_to_human','conflict_resolved')),
        outcome_reason  TEXT,
        policy_version  TEXT,
        score_json      JSONB,
        governance_token_id UUID,
        resolved_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX idx_decisions_org ON decisions (org_id, created_at DESC);
    CREATE INDEX idx_decisions_correlation ON decisions (correlation_id);
    CREATE INDEX idx_decisions_partition ON decisions (org_id, partition_key);

    CREATE TABLE hitl_queue (
        hitl_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id         TEXT NOT NULL REFERENCES tenants(org_id),
        decision_id    UUID REFERENCES decisions(decision_id),
        correlation_id UUID NOT NULL,
        partition_key  TEXT NOT NULL,
        trigger_reason TEXT NOT NULL,
        proposal_json  JSONB NOT NULL,
        score_json     JSONB,
        status         TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN
                         ('pending','approved','rejected','modified','expired')),
        verdict_by     TEXT,
        verdict_json   JSONB,
        verdict_at     TIMESTAMPTZ,
        expires_at     TIMESTAMPTZ,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX idx_hitl_org_pending
        ON hitl_queue (org_id, created_at DESC) WHERE status = 'pending';

    -- ========================================================
    -- MEMORY + KNOWLEDGE GRAPH
    -- ========================================================
    CREATE TABLE memory_records (
        record_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id       TEXT NOT NULL REFERENCES tenants(org_id),
        namespace    TEXT NOT NULL,
        tier         TEXT NOT NULL
                     CHECK (tier IN ('episodic','semantic','procedural','org','audit')),
        content_hash TEXT NOT NULL,
        content_text TEXT NOT NULL,
        metadata_json JSONB NOT NULL DEFAULT '{}',
        superseded_by UUID REFERENCES memory_records(record_id),
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by_agent TEXT NOT NULL
    );
    CREATE INDEX idx_memory_org_ns ON memory_records (org_id, namespace);
    CREATE INDEX idx_memory_hash ON memory_records (org_id, content_hash);
    CREATE INDEX idx_memory_fts
        ON memory_records USING GIN (to_tsvector('english', content_text));

    CREATE TABLE kg_nodes (
        node_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id      TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        external_id TEXT,
        name        TEXT,
        attrs_json  JSONB NOT NULL DEFAULT '{}',
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX idx_kg_nodes_org_type ON kg_nodes (org_id, entity_type);

    CREATE TABLE kg_edges (
        edge_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id     TEXT NOT NULL,
        src_id     UUID NOT NULL REFERENCES kg_nodes(node_id),
        rel        TEXT NOT NULL,
        dst_id     UUID NOT NULL REFERENCES kg_nodes(node_id),
        attrs_json JSONB NOT NULL DEFAULT '{}',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX idx_kg_edges_src ON kg_edges (org_id, src_id, rel);
    CREATE INDEX idx_kg_edges_dst ON kg_edges (org_id, dst_id, rel);

    -- ========================================================
    -- AUDIT (append-only)
    -- ========================================================
    CREATE TABLE audit_log (
        audit_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        event_id        UUID NOT NULL UNIQUE,
        org_id          TEXT NOT NULL,
        tenant_id       TEXT NOT NULL,
        correlation_id  UUID NOT NULL,
        causation_id    UUID,
        source_agent_id TEXT,
        authority_level TEXT,
        governance_token_id UUID,
        action_type     TEXT NOT NULL,
        inputs_hash     TEXT,
        outputs_hash    TEXT,
        result          TEXT NOT NULL,
        result_reason   TEXT,
        occurred_at     TIMESTAMPTZ NOT NULL,
        recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX idx_audit_org_time ON audit_log (org_id, occurred_at DESC);
    CREATE INDEX idx_audit_correlation ON audit_log (correlation_id);

    -- ========================================================
    -- INTEGRATIONS (credential references; secrets live in the secrets manager)
    -- ========================================================
    CREATE TABLE tenant_integrations (
        integration_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id   TEXT NOT NULL REFERENCES tenants(org_id),
        adapter  TEXT NOT NULL,
        status   TEXT NOT NULL DEFAULT 'connected'
                 CHECK (status IN ('connected','disconnected','error')),
        config_json JSONB NOT NULL DEFAULT '{}',
        first_launched BOOLEAN NOT NULL DEFAULT false,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (org_id, adapter)
    );
    """
    )

    # ---- Row-level security on every tenant table (FORCE so owner is subject) ----
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
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', t);
            EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', t);
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

    # ---- Append-only audit: block UPDATE/DELETE regardless of role ----
    op.execute(
        """
    CREATE OR REPLACE FUNCTION skylize_prevent_mutation()
    RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'audit_log is append-only: % is not permitted', TG_OP;
    END;
    $$ LANGUAGE plpgsql;

    CREATE TRIGGER audit_log_append_only
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION skylize_prevent_mutation();
    """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log;")
    op.execute("DROP FUNCTION IF EXISTS skylize_prevent_mutation();")
    for table in [
        "tenant_integrations",
        "audit_log",
        "kg_edges",
        "kg_nodes",
        "memory_records",
        "hitl_queue",
        "decisions",
        "budget_ledger",
        "kill_switch_state",
        "agent_live_state",
        "governance_tokens",
        "agent_contracts",
        "tenant_users",
        "tenants",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
