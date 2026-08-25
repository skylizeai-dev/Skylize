"""Seed model_pricing for claude-fable-5 / claude-mythos-5 (ADR-0006)

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-30

Migration 0013 seeded the global (org_id IS NULL) price list from the
provider's published pricing pages but predates the Claude Fable 5 / Claude
Mythos 5 rows being required here. This migration adds exactly those two
models, in 0013's row shape, from the same official sources. No existing row
is modified — 0013's rows may already have priced real calls.

SOURCES (both retrieved 2026-07-30):
  * https://platform.claude.com/docs/en/about-claude/pricing.md
      ("Model pricing" table: base input / output USD per MTok)
  * https://platform.claude.com/docs/en/about-claude/models/overview.md
      (model ids: confirms "claude-fable-5" and "claude-mythos-5" are the
       dateless pinned Claude API ids — for both models the "Claude API ID"
       and "Claude API alias" columns carry the identical dateless string)

Verified published prices (USD per MTok, input / output):
  claude-fable-5     10.00 / 50.00
  claude-mythos-5    10.00 / 50.00  (identical pricing; the pricing page lists
                                     Claude Mythos 5 as "limited availability"
                                     via Project Glasswing — an access
                                     restriction, not a price difference)

Effective-dated versions: NEITHER model has one. The pricing page publishes a
single undated price for each (the only effective-dated pricing on the page is
Claude Sonnet 5's introductory price, already seeded by 0013 as two versions).
Each model therefore gets one version-1 row, effective_to NULL.

Prices are stored as exact integers in micro-USD per MTok ($10.00/MTok ==
10_000_000), same convention as 0013. effective_from is the
price-verification date (2026-07-30T00:00:00Z); no earlier effective date is
invented.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VERIFIED_AT = datetime(2026, 7, 30, tzinfo=timezone.utc)

# (model, input_micros_per_mtok, output_micros_per_mtok, version,
#  effective_from, effective_to) — provider is "anthropic" for every row.
SEED_PRICES: list[tuple[str, int, int, int, datetime, datetime | None]] = [
    ("claude-fable-5", 10_000_000, 50_000_000, 1, _VERIFIED_AT, None),
    ("claude-mythos-5", 10_000_000, 50_000_000, 1, _VERIFIED_AT, None),
]

_PROVIDER = "anthropic"


def upgrade() -> None:
    for model, in_p, out_p, version, eff_from, eff_to in SEED_PRICES:
        op.execute(
            sa.text(
                """
                INSERT INTO model_pricing (org_id, provider, model,
                    input_price_micros_per_mtok, output_price_micros_per_mtok,
                    currency, version, effective_from, effective_to)
                VALUES (NULL, :provider, :model, :in_p, :out_p, 'USD',
                        :version, :eff_from, :eff_to)
                """
            ).bindparams(
                provider=_PROVIDER,
                model=model,
                in_p=in_p,
                out_p=out_p,
                version=version,
                eff_from=eff_from,
                eff_to=eff_to,
            )
        )


def downgrade() -> None:
    # model_pricing is mutable reference data (no append-only guard); removing
    # exactly the rows this migration inserted restores the 0017 state.
    for model, _in_p, _out_p, version, _eff_from, _eff_to in SEED_PRICES:
        op.execute(
            sa.text(
                """
                DELETE FROM model_pricing
                WHERE org_id IS NULL AND provider = :provider
                  AND model = :model AND version = :version
                """
            ).bindparams(provider=_PROVIDER, model=model, version=version)
        )
