"""ai_cost_ledger + model_pricing — billing-grade LLM cost attribution (ADR-0006)

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-25

The third money ledger (ADR-0006). It is NOT the token ``run_ledger`` (an
in-flight per-run token ceiling, RAM/Redis only) and NOT ``budget_ledger``
(business spend against a ceiling, migration 0001). This is *LLM cost expressed
in money*: one immutable row per real provider call, written transactionally at
the single point where real provider usage is observed (the concrete LLM gateway
adapter — see ADR-0006 §"Seam"). It is deliberately NOT derived from Langfuse
(observability-grade: sampled, retention-bounded) nor from the event bus
(delivery semantics under repair).

Two tables:

  * ``ai_cost_ledger`` — tenant-scoped (RLS, mirroring ``budget_ledger``) and
    DB-level append-only (least-privilege grant + a BEFORE UPDATE/DELETE trigger,
    mirroring ``audit_log``). Corrections are made by REVERSING entries
    (``entry_type='reversal'`` with negated tokens/cost), never by UPDATE.

  * ``model_pricing`` — versioned, effective-dated provider prices. Platform
    reference data (no RLS, like ``api_keys``); a nullable ``org_id`` carries a
    future per-tenant (BYOK-negotiated) override with global-price fallback. Each
    ledger row SNAPSHOTS the unit prices + ``pricing_version`` it used, so a later
    price change never retroactively alters recorded history.

Money unit: cost is stored in ``cost_micros`` — millionths of one currency unit,
matching the gateway's own ``LLMGenerateResponse.cost_usd_micros`` contract
(adapters/llm/gateway.py). Storing whole minor units (cents) PER ROW would
silently discard the sub-cent value of small calls — exactly the drift ADR-0006
forbids — so the ledger keeps micro resolution and cents are derived ONCE at
invoice aggregation. Unit prices are stored per 1e6 tokens (``*_per_mtok``) so
every real quoted price is an exact integer. See ADR-0006 §"Money & rounding".
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"


def upgrade() -> None:
    # asyncpg executes only one statement per op.execute() call; every DDL
    # statement is therefore its own call (the DO block below is one statement).

    # ------------------------------------------------------------------
    # model_pricing — versioned, effective-dated provider prices.
    # Platform reference data (no RLS). org_id NULL => global/platform list
    # price (the fallback); org_id set => a per-tenant BYOK-negotiated override.
    # Prices are per 1,000,000 tokens in micro-currency so they are exact ints:
    #   $3.00 / Mtok  == 3_000_000 micro-USD / Mtok.
    # ------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE TABLE model_pricing (
            pricing_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id      TEXT REFERENCES tenants(org_id),   -- NULL => global fallback
            provider    TEXT NOT NULL,
            model       TEXT NOT NULL,                     -- concrete provider model id
            input_price_micros_per_mtok  BIGINT NOT NULL CHECK (input_price_micros_per_mtok  >= 0),
            output_price_micros_per_mtok BIGINT NOT NULL CHECK (output_price_micros_per_mtok >= 0),
            currency    TEXT NOT NULL DEFAULT 'USD',
            version     INTEGER NOT NULL,
            effective_from TIMESTAMPTZ NOT NULL,
            effective_to   TIMESTAMPTZ,                    -- NULL => open-ended (current)
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT model_pricing_period CHECK (
                effective_to IS NULL OR effective_to > effective_from
            )
        )
    """))

    # One version number per (provider, model) price line, per scope (global vs a
    # given tenant). COALESCE folds the nullable org_id so global rows collide on
    # version too (SQL NULLs would otherwise all be distinct).
    op.execute(sa.text(
        "CREATE UNIQUE INDEX uq_model_pricing_scope_version "
        "ON model_pricing (provider, model, COALESCE(org_id, ''), version)"
    ))
    # Effective-dated point lookup: latest effective_from <= occurred_at.
    op.execute(sa.text(
        "CREATE INDEX idx_model_pricing_lookup "
        "ON model_pricing (provider, model, COALESCE(org_id, ''), effective_from DESC)"
    ))

    # ------------------------------------------------------------------
    # ai_cost_ledger — one immutable row per real provider call.
    # entry_type='charge'   : tokens/cost >= 0 (a real usage record)
    # entry_type='reversal' : tokens/cost <= 0 and reverses_entry_id set
    #                         (a correction — history is fixed by APPENDING, never
    #                         by UPDATE).
    # ------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE TABLE ai_cost_ledger (
            entry_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id         TEXT NOT NULL REFERENCES tenants(org_id),
            correlation_id UUID NOT NULL,
            agent_id       TEXT NOT NULL,
            run_id         UUID NOT NULL,          -- governance token id == run key
            step_id        UUID,                   -- tool-use turn within the run (nullable)

            provider       TEXT NOT NULL,
            model          TEXT NOT NULL,          -- concrete provider model id
            input_tokens   BIGINT NOT NULL,
            output_tokens  BIGINT NOT NULL,

            -- price snapshot (from model_pricing at occurred_at) — makes the row
            -- reproducible and immune to later price changes.
            input_price_micros_per_mtok  BIGINT NOT NULL,
            output_price_micros_per_mtok BIGINT NOT NULL,
            pricing_version INTEGER NOT NULL,

            cost_micros    BIGINT NOT NULL,        -- millionths of `currency`
            currency       TEXT NOT NULL DEFAULT 'USD',

            byok           BOOLEAN NOT NULL DEFAULT false,  -- tenant's own provider key

            -- reconciliation against a provider invoice line
            billing_period       TEXT NOT NULL,             -- e.g. '2026-07'
            provider_invoice_ref TEXT,                       -- set when reconciled

            -- idempotency: the provider's response/request id for the served call
            -- (globally unique per real charge). A retried WRITE collapses to one
            -- row; two genuine provider charges keep two ids.
            idempotency_key TEXT NOT NULL,

            -- correction model
            entry_type       TEXT NOT NULL DEFAULT 'charge'
                             CHECK (entry_type IN ('charge','reversal')),
            reverses_entry_id UUID REFERENCES ai_cost_ledger(entry_id),

            occurred_at    TIMESTAMPTZ NOT NULL,   -- when the provider served
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

            CONSTRAINT ai_cost_ledger_sign CHECK (
                (entry_type = 'charge'
                    AND input_tokens >= 0 AND output_tokens >= 0 AND cost_micros >= 0
                    AND reverses_entry_id IS NULL)
                OR
                (entry_type = 'reversal'
                    AND input_tokens <= 0 AND output_tokens <= 0 AND cost_micros <= 0
                    AND reverses_entry_id IS NOT NULL)
            )
        )
    """))

    # Idempotency key — one recorded row per logical provider call, per tenant.
    op.execute(sa.text(
        "CREATE UNIQUE INDEX uq_ai_cost_ledger_idem "
        "ON ai_cost_ledger (org_id, idempotency_key)"
    ))
    # Reconciliation: SUM(cost_micros) grouped by provider + period.
    op.execute(sa.text(
        "CREATE INDEX idx_ai_cost_ledger_reconcile "
        "ON ai_cost_ledger (org_id, provider, billing_period)"
    ))
    # Tenant time-ordered listing.
    op.execute(sa.text(
        "CREATE INDEX idx_ai_cost_ledger_org_time "
        "ON ai_cost_ledger (org_id, occurred_at DESC)"
    ))

    # ------------------------------------------------------------------
    # Row-level security on ai_cost_ledger — mirrors budget_ledger EXACTLY
    # (ENABLE + FORCE so even the table owner is subject to the policy;
    # migration 0001). model_pricing has NO RLS (platform reference data).
    # ------------------------------------------------------------------
    op.execute(sa.text("ALTER TABLE ai_cost_ledger ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE ai_cost_ledger FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
        CREATE POLICY tenant_isolation ON ai_cost_ledger
        FOR ALL
        USING (org_id = current_setting('skylize.org_id', true))
        WITH CHECK (org_id = current_setting('skylize.org_id', true))
    """))

    # ------------------------------------------------------------------
    # DB-level append-only guard for ai_cost_ledger (belt: least-privilege
    # grant below withholds UPDATE/DELETE; suspenders: this trigger blocks it
    # even for a role that somehow held the grant). Mirrors the audit_log
    # append-only trigger (migration 0001) but with a table-accurate message.
    # ------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION skylize_prevent_ledger_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'ai_cost_ledger is append-only: % is not permitted (correct via a reversal entry)',
                TG_OP;
        END;
        $$ LANGUAGE plpgsql
    """))
    op.execute(sa.text("""
        CREATE TRIGGER ai_cost_ledger_append_only
            BEFORE UPDATE OR DELETE ON ai_cost_ledger
            FOR EACH ROW EXECUTE FUNCTION skylize_prevent_ledger_mutation()
    """))

    # ------------------------------------------------------------------
    # Grants for the non-superuser runtime role (subject to RLS).
    #   ai_cost_ledger : SELECT + INSERT only  → append-only at the privilege
    #                    layer as well (mirrors audit_log in migration 0003).
    #   model_pricing  : full DML (mutable reference data, seeded by ops).
    # ------------------------------------------------------------------
    op.execute(f"GRANT SELECT, INSERT ON ai_cost_ledger TO {_APP_ROLE};")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON model_pricing TO {_APP_ROLE};")

    # ------------------------------------------------------------------
    # Seed: intentionally EMPTY. Real provider prices are ops-managed and are
    # NOT fabricated here (project empty-value convention). Seed model_pricing
    # via an ops task / bootstrap before recording any cost.
    # ------------------------------------------------------------------
    # (no INSERTs — see ADR-0006 §"Pricing")


def downgrade() -> None:
    op.execute(sa.text(
        "DROP TRIGGER IF EXISTS ai_cost_ledger_append_only ON ai_cost_ledger"
    ))
    op.execute(f"REVOKE ALL ON ai_cost_ledger FROM {_APP_ROLE};")
    op.execute(f"REVOKE ALL ON model_pricing FROM {_APP_ROLE};")
    op.execute(sa.text("DROP TABLE IF EXISTS ai_cost_ledger CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS model_pricing CASCADE"))
    # Drop the trigger function only after the table (and its trigger) are gone.
    op.execute(sa.text("DROP FUNCTION IF EXISTS skylize_prevent_ledger_mutation()"))
