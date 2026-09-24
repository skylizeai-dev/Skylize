"""model_routing_rules — the ORG-SCOPED logical-model routing table (RLS)

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-20

Re-chained from 0030 to 0031 during the origin/main sync that brought
0031_backfill_owner_principal.py in as a sibling of 0030: both originally
pointed at 0030, giving alembic two heads. Content unchanged; only this
migration's place in the chain moved.

WHAT THIS IS. The LLM gateway contract (adapters/llm/gateway.py:36) says the
`model` an agent asks for is a LOGICAL name -- "default", "fast", "reasoning" --
and that "the gateway maps logical -> concrete per tenant policy". The concrete
half of that map is real and lives in the adapter
(adapters/llm/anthropic_adapter.py:267-271, from Settings
`llm_model_default` / `llm_model_fast` / `llm_model_reasoning`,
config.py:311-313). The TENANT POLICY half had no store at all: there was
nowhere for an org to say "route my reasoning class to `fast` this month". This
table is that store, and nothing more.

SCOPE, deliberately narrow:
  * `routing_class` -- the logical class an agent names. CHECK-constrained to
    the THREE names the adapter's `_model_map` actually accepts; a fourth value
    cannot enter through SQL because the adapter would raise `ValueError` on it
    (`_concrete_model`, anthropic_adapter.py:368-374).
  * `target_logical_model` -- the logical name the class resolves to. Also one
    of the same three: this table remaps LOGICAL to LOGICAL. It deliberately
    does NOT hold a concrete provider model id. Concrete ids are Settings-owned
    and change with provider releases; letting a tenant row name one would put
    an unvalidated provider string on the egress path.
  * `fallback_logical_model` -- NULLable. The class to try when the target is
    refused (unpriced, provider unavailable). NULL means no fallback, which is
    the fail-closed reading: refuse rather than silently spend on a model the
    org did not choose.

WHAT IS DELIBERATELY ABSENT. There is no `traffic_share_pct` column, no
`latency_ms`, and no `context_window`. Traffic share is DERIVABLE from
`ai_cost_ledger` (migration 0012: one immutable row per real provider call,
carrying `model` and `org_id`) -- storing it as configuration would create a
second, fictional source of truth that drifts from what actually ran. Latency
and context window have NO backend source whatsoever today; a column for either
could only ever be filled by hand with a number nobody measured.

SHAPE: PRIMARY KEY (org_id, effective_from), the same effective-dated shape
migration 0028 gives `org_autonomy_mode` and, through it, migration 0014 gives
`org_spend_ceiling`. The rule set IN FORCE is the row set with the greatest
`effective_from` at or before now, so a rule set once stays in force until a
newer row supersedes it and history is kept rather than overwritten.

Because a routing CHANGE is one atomic decision covering several classes, the
per-class row is keyed by (org_id, effective_from, routing_class): every class
written at the same instant is one generation, and the read resolves the single
greatest instant and returns every row at it. A partial generation is therefore
impossible to read as a merge of two different decisions.

FAIL CLOSED: a MISSING row means NO OVERRIDE -- the class resolves to itself,
i.e. exactly what the adapter does today. That default is NOT a column DEFAULT
and NOT a seeded row; it lives in the read path (`dal/model_routing.py`) for the
same reason migration 0028 gives at its own Fail-closed note: "nobody has set
this" and "somebody chose the identity mapping" must stay distinguishable in
the table while resolving to the same safe behaviour.

Mutability + isolation: MUTABLE CONFIG, so no append-only trigger. RLS is
modelled EXACTLY on `org_autonomy_mode` (migration 0028) and through it on
`ai_cost_ledger` (migration 0012:178-185): ENABLE + FORCE ROW LEVEL SECURITY so
even the table owner is a policy subject, and a single `tenant_isolation`
FOR ALL policy on `current_setting('skylize.org_id')`. The non-superuser
`skylize_app` role is therefore a genuine RLS subject: one org can neither read
nor write another org's routing.

Seed: the table is created EMPTY. Every org starts on the identity mapping by
the fail-closed read, which is the correct routing for an org whose owner has
not yet chosen one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"


def upgrade() -> None:
    # asyncpg executes only one statement per op.execute() call; each DDL
    # statement is therefore its own call.

    # ------------------------------------------------------------------
    # model_routing_rules — one row per (org, effective instant, class).
    # Org-scoped, effective-dated, logical -> logical only.
    # ------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE TABLE model_routing_rules (
            org_id                 TEXT NOT NULL REFERENCES tenants(org_id),
            effective_from         TIMESTAMPTZ NOT NULL DEFAULT now(),
            routing_class          TEXT NOT NULL CHECK (routing_class IN ('default', 'fast', 'reasoning')),
            target_logical_model   TEXT NOT NULL CHECK (target_logical_model IN ('default', 'fast', 'reasoning')),
            fallback_logical_model TEXT CHECK (fallback_logical_model IN ('default', 'fast', 'reasoning')),
            created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (org_id, effective_from, routing_class),
            CONSTRAINT model_routing_rules_fallback_differs CHECK (
                fallback_logical_model IS NULL
                OR fallback_logical_model <> target_logical_model
            )
        )
    """))

    op.execute(sa.text(
        "COMMENT ON COLUMN model_routing_rules.routing_class IS "
        "'The logical class an agent names on the gateway request. The three "
        "values are exactly the keys of AnthropicAdapter._model_map "
        "(adapters/llm/anthropic_adapter.py:267-271); the CHECK is what stops a "
        "fourth value the adapter would reject at egress from entering here.'"
    ))
    op.execute(sa.text(
        "COMMENT ON COLUMN model_routing_rules.target_logical_model IS "
        "'The logical model the class resolves to. LOGICAL, never a concrete "
        "provider model id: concrete ids are Settings-owned (config.py:311-313) "
        "and a tenant row naming one would put an unvalidated provider string "
        "on the egress path.'"
    ))
    op.execute(sa.text(
        "COMMENT ON COLUMN model_routing_rules.fallback_logical_model IS "
        "'The class to try when the target is refused. NULL means NO fallback "
        "-- refuse rather than silently spend on a model the org did not "
        "choose. A MISSING row entirely means no override: the class resolves "
        "to itself, which is what the adapter already does.'"
    ))

    # Effective-dated resolution: greatest effective_from at or before now, per
    # org. The PK btree already leads on (org_id, effective_from), so the
    # reverse range scan is served without an extra index — the same reasoning
    # dal/org_autonomy_mode.py gives for its own read.

    # ------------------------------------------------------------------
    # Row-level security — modelled EXACTLY on org_autonomy_mode (migration
    # 0028) and through it on ai_cost_ledger (migration 0012:178-185).
    # ------------------------------------------------------------------
    op.execute(sa.text("ALTER TABLE model_routing_rules ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE model_routing_rules FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
        CREATE POLICY tenant_isolation ON model_routing_rules
        FOR ALL
        USING (org_id = current_setting('skylize.org_id', true))
        WITH CHECK (org_id = current_setting('skylize.org_id', true))
    """))

    # ------------------------------------------------------------------
    # Grants for the non-superuser runtime role (subject to RLS). Mutable
    # config, so SELECT + INSERT + UPDATE.
    #
    # The REVOKE is not redundant, for the reason migration 0028 states:
    # migration 0003 set ALTER DEFAULT PRIVILEGES granting skylize_app `arwd`
    # on every table created afterwards, so this table arrives with DELETE
    # already granted. Without the REVOKE the app role could erase an org's
    # routing history, which is the evidence of which model an org's spend was
    # incurred against.
    # ------------------------------------------------------------------
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON model_routing_rules TO {_APP_ROLE};")
    op.execute(f"REVOKE DELETE ON model_routing_rules FROM {_APP_ROLE};")

    # ------------------------------------------------------------------
    # Seed: intentionally EMPTY. An org with no row routes every class to
    # itself (the identity mapping), resolved in the read path.
    # ------------------------------------------------------------------
    # (no INSERTs)


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON model_routing_rules FROM {_APP_ROLE};")
    op.execute(sa.text("DROP TABLE IF EXISTS model_routing_rules CASCADE"))
