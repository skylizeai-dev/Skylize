"""Seed model_pricing with verified published Anthropic prices (ADR-0006)

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-28

model_pricing was deliberately seeded EMPTY by migration 0012 (no fabricated
prices). This migration seeds the global (org_id IS NULL) price list from the
provider's OFFICIAL published pricing pages, so the cost ledger can fail open
for real calls instead of failing closed on every write.

SOURCES (both retrieved 2026-07-28):
  * https://platform.claude.com/docs/en/about-claude/pricing.md
      ("Model pricing" table: base input / output USD per MTok)
  * https://platform.claude.com/docs/en/about-claude/models/overview.md
      (model ids: confirms "claude-sonnet-4-6", "claude-opus-4-6" are the
       dateless pinned API ids and "claude-haiku-4-5-20251001" is the full id
       with alias "claude-haiku-4-5")

Verified published prices (USD per MTok, input / output):
  claude-sonnet-4-6            3.00 / 15.00   (configured: llm_model_default)
  claude-haiku-4-5-20251001    1.00 /  5.00   (configured: llm_model_fast)
  claude-haiku-4-5             1.00 /  5.00   (alias form; the provider's
                                              response.model may report either
                                              form, so both are priced)
  claude-opus-4-6              5.00 / 25.00   (configured: llm_model_reasoning)
  claude-opus-4-7              5.00 / 25.00   (successor row)
  claude-opus-4-8              5.00 / 25.00   (successor row)
  claude-opus-5                5.00 / 25.00   (current Opus successor row)
  claude-sonnet-5              2.00 / 10.00   through 2026-08-31 (published
                                              introductory pricing), then
                               3.00 / 15.00   from 2026-09-01 (published
                                              standard pricing) — seeded as two
                                              effective-dated versions.

DISCREPANCIES vs the Settings price floats (config.py), reported per owner
decision D2 — the PUBLISHED value is seeded, not the float:
  * llm_price_haiku_in/out = 0.80 / 4.0  but published Haiku 4.5 price is
    1.00 / 5.00. (0.80/4.0 is the published price of RETIRED Haiku 3.5.)
  * llm_price_opus_in/out = 15.0 / 75.0  but published Opus 4.6 price is
    5.00 / 25.00. (15/75 is the published price of DEPRECATED Opus 4.1.)
  * llm_price_sonnet_in/out = 3.0 / 15.0 matches the published Sonnet 4.6
    price exactly.

Successor rows beyond the three configured models are seeded per Stage-2
step 15: the table is keyed by (provider, model), extra rows cost nothing,
and they make a future model upgrade a config change rather than a migration.

Cache-read / cache-write token classes ARE priced distinctly by the provider
(0.1x reads, 1.25x/2x writes) but the model_pricing schema has only
input/output columns; per owner rule the schema is NOT extended here — the
gap is reported for the owner.

Prices are stored as exact integers in micro-USD per MTok ($3.00/MTok ==
3_000_000). effective_from for the current rows is the price-verification
date (2026-07-28T00:00:00Z); no earlier effective date is invented.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VERIFIED_AT = datetime(2026, 7, 28, tzinfo=timezone.utc)
_SONNET5_STANDARD_FROM = datetime(2026, 9, 1, tzinfo=timezone.utc)

# (model, input_micros_per_mtok, output_micros_per_mtok, version,
#  effective_from, effective_to) — provider is "anthropic" for every row.
SEED_PRICES: list[tuple[str, int, int, int, datetime, datetime | None]] = [
    # Configured models (Settings llm_model_default / _fast / _reasoning)
    ("claude-sonnet-4-6", 3_000_000, 15_000_000, 1, _VERIFIED_AT, None),
    ("claude-haiku-4-5-20251001", 1_000_000, 5_000_000, 1, _VERIFIED_AT, None),
    ("claude-haiku-4-5", 1_000_000, 5_000_000, 1, _VERIFIED_AT, None),
    ("claude-opus-4-6", 5_000_000, 25_000_000, 1, _VERIFIED_AT, None),
    # Current-generation successors (nothing selects these today)
    ("claude-opus-4-7", 5_000_000, 25_000_000, 1, _VERIFIED_AT, None),
    ("claude-opus-4-8", 5_000_000, 25_000_000, 1, _VERIFIED_AT, None),
    ("claude-opus-5", 5_000_000, 25_000_000, 1, _VERIFIED_AT, None),
    # Sonnet 5: published introductory price through 2026-08-31, then the
    # published standard price — two effective-dated versions.
    ("claude-sonnet-5", 2_000_000, 10_000_000, 1, _VERIFIED_AT, _SONNET5_STANDARD_FROM),
    ("claude-sonnet-5", 3_000_000, 15_000_000, 2, _SONNET5_STANDARD_FROM, None),
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
    # exactly the rows this migration inserted restores the 0012 empty state.
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
