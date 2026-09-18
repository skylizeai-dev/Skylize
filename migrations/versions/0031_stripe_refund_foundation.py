"""org_stripe_accounts + org_refund_authority_limits + org_refund_review_thresholds

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-18

Design: docs/06_integrations/stripe_connector_design.md 4.0.2, 7.5.2, 7.5.3.
NOT YET SIGNED OFF in that document's Sign-off section - the numbers this
migration seeds are NONE (all three tables are created EMPTY), and the schema
itself was re-verified fresh against this HEAD (post-PR-#14) as part of
implementing the first Stripe-adjacent code in this repository.

org_stripe_accounts (4.0.2). Deliberately lightweight - no bearer token is
ever stored (Q2.1i, discard decision at 4.0.1): only `stripe_account_id`
(acct_..., an identifier, not a secret), `livemode`, and `scope` as GRANTED
by Stripe. `deauthorized_at` closes gap 4 (revocation state) WITHOUT a hard
delete - deleting the row would destroy the audit trail of an account that
once held authority, the same reasoning `gcp_wif_connections.set_connection_state`
uses (dal/gcp_wif.py:272-273) to never delete a broken federation row either.

  * unique on (stripe_account_id) - acct_ ids are globally unique at Stripe
    across live and test, so this also prevents one account being claimed by
    two orgs.
  * partial unique on (org_id, livemode) WHERE deauthorized_at IS NULL - at
    most one live and one test connection per org.
  * RLS ENABLE + FORCE, tenant_isolation on current_setting('skylize.org_id'),
    modelled on migration 0007's org_credentials (0007:59-72). No DELETE
    grant - the row is retired via deauthorized_at, never removed.

org_refund_authority_limits + org_refund_review_thresholds (7.5.2, 7.5.3).
Two tables, not one - see 7.5.1 for why one org-scoped value must not be
duplicated across authority rows where it could silently disagree with
itself. BOTH tables are denominated in currency MINOR units (cents), NOT
micro-units (7.5.0's own UNIT WARNING) - this is the opposite unit from
org_spend_ceiling.ceiling_micros (migration 0014), and conflating the two
units is exactly the mistake ADR-0006 exists to prevent.

Structural template borrowed from org_spend_ceiling (migration 0014), and
the unit deliberately inverted (0014 is micro-USD LLM spend; these are
minor-unit business money): ENABLE + FORCE ROW LEVEL SECURITY so even the
table owner is a policy subject (0014:99-100), the single tenant_isolation
FOR ALL policy (0014:101-106), SELECT/INSERT/UPDATE for skylize_app with NO
DELETE path (0014:113), and the EMPTY seed with fail-closed-on-missing-row
as the design (0014:44-46,48-50,116-120) - a missing row DENIES for
org_refund_authority_limits and ROUTES TO A HUMAN for
org_refund_review_thresholds (7.5.3's fail-closed-means-review-not-deny
distinction), never falls back to a platform default, another currency, or
another authority level.

`authority_level`'s five values and their order are NOT redefined here -
they are the canonical AUTHORITY_RANK ladder (contracts/base.py:70-76). The
CHECK enumerates them explicitly rather than joining a lookup table, the
same choice migration 0024 makes for its own enums
(migrations/versions/0024_gcp_wif_connections.py:151-165).

NO NUMBER IS SEEDED IN ANY OF THE THREE TABLES. The refund cap numbers
(Q2.1j) and review thresholds are an owner/ops configuration input at
deployment time, not a design blocker (7.5.6) - exactly as org_spend_ceiling
seeds nothing (0014 D7).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"


def upgrade() -> None:
    # ------------------------------------------------------------------
    # org_stripe_accounts (4.0.2)
    # ------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE TABLE org_stripe_accounts (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id            TEXT NOT NULL REFERENCES tenants(org_id),
            stripe_account_id TEXT NOT NULL,
            livemode          BOOLEAN NOT NULL,
            scope             TEXT NOT NULL,
            connected_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            deauthorized_at   TIMESTAMPTZ
        )
    """))

    op.execute(sa.text(
        "CREATE UNIQUE INDEX idx_org_stripe_accounts_account_id "
        "ON org_stripe_accounts (stripe_account_id)"
    ))
    op.execute(sa.text(
        "CREATE UNIQUE INDEX idx_org_stripe_accounts_live_per_org "
        "ON org_stripe_accounts (org_id, livemode) "
        "WHERE deauthorized_at IS NULL"
    ))

    op.execute(sa.text(
        "COMMENT ON COLUMN org_stripe_accounts.stripe_account_id IS "
        "'acct_...; an IDENTIFIER, not a bearer secret. Authentication is the "
        "platform secret key plus a Stripe-Account header naming this value, "
        "never a per-tenant token (design 3.0.3, 4.0.1).'"
    ))
    op.execute(sa.text(
        "COMMENT ON COLUMN org_stripe_accounts.scope IS "
        "'As GRANTED by Stripe, never as requested. The platform attenuation "
        "invariant reads this to compute the effective permission as an "
        "intersection (design 4.0.2, integration_inputs.md:31-35).'"
    ))
    op.execute(sa.text(
        "COMMENT ON COLUMN org_stripe_accounts.deauthorized_at IS "
        "'NULL = connected. Set, never deleted, on disconnect - the row is the "
        "audit trail of an account that once held authority (design 4.0.2).'"
    ))

    op.execute(sa.text("ALTER TABLE org_stripe_accounts ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE org_stripe_accounts FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
        CREATE POLICY tenant_isolation ON org_stripe_accounts
        FOR ALL
        USING (org_id = current_setting('skylize.org_id', true))
        WITH CHECK (org_id = current_setting('skylize.org_id', true))
    """))
    op.execute(sa.text(
        f"GRANT SELECT, INSERT, UPDATE ON org_stripe_accounts TO {_APP_ROLE}"
    ))

    # ------------------------------------------------------------------
    # org_refund_authority_limits (7.5.2)
    # ------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE TABLE org_refund_authority_limits (
            org_id           TEXT NOT NULL REFERENCES tenants(org_id),
            currency         TEXT NOT NULL,
            authority_level  TEXT NOT NULL,
            max_refund_minor BIGINT NOT NULL CHECK (max_refund_minor >= 0),
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (org_id, currency, authority_level),
            CONSTRAINT authority_level_known CHECK (
                authority_level IN ('worker','manager','director','vp','executive')
            )
        )
    """))
    op.execute(sa.text(
        "COMMENT ON COLUMN org_refund_authority_limits.max_refund_minor IS "
        "'currency MINOR units (cents), the SAME unit as SpendEnvelope.ceiling_minor "
        "and ToolSpendProfile.amount_field. NOT micro-units, NOT the unit of "
        "org_spend_ceiling.ceiling_micros.'"
    ))
    op.execute(sa.text("ALTER TABLE org_refund_authority_limits ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE org_refund_authority_limits FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
        CREATE POLICY tenant_isolation ON org_refund_authority_limits
        FOR ALL
        USING (org_id = current_setting('skylize.org_id', true))
        WITH CHECK (org_id = current_setting('skylize.org_id', true))
    """))
    op.execute(sa.text(
        f"GRANT SELECT, INSERT, UPDATE ON org_refund_authority_limits TO {_APP_ROLE}"
    ))

    # ------------------------------------------------------------------
    # org_refund_review_thresholds (7.5.3)
    # ------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE TABLE org_refund_review_thresholds (
            org_id                    TEXT NOT NULL REFERENCES tenants(org_id),
            currency                  TEXT NOT NULL,
            review_above_minor        BIGINT NOT NULL CHECK (review_above_minor >= 0),
            created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (org_id, currency)
        )
    """))
    op.execute(sa.text(
        "COMMENT ON COLUMN org_refund_review_thresholds.review_above_minor IS "
        "'currency MINOR units (cents). A refund at or above this amount is "
        "routed to a human regardless of the requesting agent authority level. "
        "NOT micro-units. A MISSING row means review everything (fail closed to "
        "the safest behaviour, design 7.5.3), not that nothing is ever reviewed.'"
    ))
    op.execute(sa.text("ALTER TABLE org_refund_review_thresholds ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE org_refund_review_thresholds FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
        CREATE POLICY tenant_isolation ON org_refund_review_thresholds
        FOR ALL
        USING (org_id = current_setting('skylize.org_id', true))
        WITH CHECK (org_id = current_setting('skylize.org_id', true))
    """))
    op.execute(sa.text(
        f"GRANT SELECT, INSERT, UPDATE ON org_refund_review_thresholds TO {_APP_ROLE}"
    ))

    # Seed: intentionally EMPTY on all three tables. Numbers are an owner/ops
    # configuration input at deployment time (design 7.5.6), not baked in here.


def downgrade() -> None:
    for table in (
        "org_refund_review_thresholds",
        "org_refund_authority_limits",
        "org_stripe_accounts",
    ):
        op.execute(sa.text(f"REVOKE ALL ON {table} FROM {_APP_ROLE}"))
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table} CASCADE"))
