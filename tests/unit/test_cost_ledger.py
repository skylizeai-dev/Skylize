"""Unit tests for the cost-ledger money math (ADR-0006).

Pure, DB-free: they pin the rounding rule, the reconciliation guarantee
(tolerance zero), and that money is Decimal — never float. DB-level guarantees
(idempotency, RLS, append-only, pricing-snapshot immutability) live in
tests/integration/test_cost_ledger_pg.py.

ALL prices here are EXPLICITLY SYNTHETIC test fixtures, not real provider
prices — see the SYNTH_* names and comments. No real price is fabricated.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from skylize.dal.cost_ledger import (
    compute_cost_micros,
    micros_to_minor,
    micros_to_unit,
)

# Synthetic per-Mtok prices in micro-currency (NOT real provider prices).
# "SYNTH_A": 3_000_000 µ/Mtok input, 15_000_000 µ/Mtok output — round numbers
# chosen so per-call costs are exact integer micros for the reconciliation test.
SYNTH_A_IN = 3_000_000
SYNTH_A_OUT = 15_000_000


# ---------------------------------------------------------------------------
# Rounding rule: HALF-UP to the nearest whole micro, residue absorbed there.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "gross_micros_per_mtok, expected_micros",
    [
        # input_tokens=1 so gross == the price; cost = price / 1e6 rounded HALF-UP.
        (500_000, 1),      # 0.5   -> 1  (HALF-UP rounds a tie AWAY from zero)
        (2_500_000, 3),    # 2.5   -> 3  (distinguishes HALF-UP from banker's -> 2)
        (499_999, 0),      # 0.4999-> 0
        (1_500_000, 2),    # 1.5   -> 2
        (1_000_000, 1),    # 1.0   -> 1  (exact, no residue)
        (1, 0),            # 1e-6  -> 0  (sub-micro rounds down)
    ],
)
def test_rounding_is_half_up_and_absorbs_subunit_residue(
    gross_micros_per_mtok: int, expected_micros: int
) -> None:
    got = compute_cost_micros(
        input_tokens=1,
        output_tokens=0,
        input_price_micros_per_mtok=gross_micros_per_mtok,
        output_price_micros_per_mtok=0,
    )
    assert got == expected_micros
    assert isinstance(got, int)  # never a float


def test_half_up_ties_go_away_from_zero_not_bankers() -> None:
    # 0.5 -> 1 and 2.5 -> 3 together exclude BOTH truncation (would give 0, 2)
    # and banker's rounding (would give 0, 2).
    assert compute_cost_micros(
        input_tokens=1, output_tokens=0,
        input_price_micros_per_mtok=500_000, output_price_micros_per_mtok=0,
    ) == 1
    assert compute_cost_micros(
        input_tokens=1, output_tokens=0,
        input_price_micros_per_mtok=2_500_000, output_price_micros_per_mtok=0,
    ) == 3


# ---------------------------------------------------------------------------
# Reconciliation: N calls sum EXACTLY to the expected invoice total (tolerance 0).
# ---------------------------------------------------------------------------

def test_reconciliation_sum_is_exact_zero_tolerance() -> None:
    # Synthetic token counts; each call's cost is an exact integer of micros.
    calls = [(1_000, 500), (2_500, 100), (10, 10), (0, 4_000), (777, 333)]

    per_call = [
        compute_cost_micros(
            input_tokens=i,
            output_tokens=o,
            input_price_micros_per_mtok=SYNTH_A_IN,
            output_price_micros_per_mtok=SYNTH_A_OUT,
        )
        for i, o in calls
    ]

    # Hand-computed expected total (exact): sum(i*3 + o*15) micros, since
    # price/1e6 == 3 (in) and 15 (out) micro per token exactly.
    expected = sum(i * 3 + o * 15 for i, o in calls)

    assert sum(per_call) == expected
    # The ledger SUM (integer micros) rounds to minor units ONCE, at the end.
    assert micros_to_minor(sum(per_call)) == (Decimal(expected) / Decimal(10_000)).quantize(Decimal(1))


def test_late_rounding_beats_per_row_cent_rounding() -> None:
    # Three sub-cent calls of 4_900 micros ($0.0049) each. Rounding each to cents
    # first (0 cents) loses everything; summing micros then rounding once yields
    # 15_000 micros -> 2 cents. Proves WHERE the residue goes.
    micros = [4_900, 4_900, 5_200]
    per_row_cents = sum(micros_to_minor(m) for m in micros)   # 0 + 0 + 1 = 1
    aggregate_cents = micros_to_minor(sum(micros))            # 15_000µ -> 2
    assert aggregate_cents == Decimal(2)
    assert aggregate_cents != per_row_cents  # per-row rounding would drift


# ---------------------------------------------------------------------------
# Money is Decimal, never float.
# ---------------------------------------------------------------------------

def test_conversions_return_decimal_not_float() -> None:
    assert isinstance(micros_to_unit(3), Decimal)
    assert isinstance(micros_to_minor(30_000), Decimal)
    assert micros_to_unit(3) == Decimal("0.000003")
    assert micros_to_minor(30_000) == Decimal(3)  # 30_000 µ == 3 cents


def test_module_money_path_has_no_float() -> None:
    # Guard the gate "no float anywhere in money paths": the DAL source must not
    # name float() in its arithmetic. (Decimal-only by construction.)
    import inspect

    import skylize.dal.cost_ledger as mod

    src = inspect.getsource(mod)
    assert "float(" not in src, "float() found in the cost-ledger money path"


# ---------------------------------------------------------------------------
# Pricing-version math: cost is a pure function of the SNAPSHOT, so a later
# price version cannot change a value computed against an earlier snapshot.
# (DB-level snapshot immutability is proven in the integration suite.)
# ---------------------------------------------------------------------------

def test_cost_is_pure_function_of_snapshot() -> None:
    v1 = compute_cost_micros(
        input_tokens=1_000, output_tokens=1_000,
        input_price_micros_per_mtok=SYNTH_A_IN, output_price_micros_per_mtok=SYNTH_A_OUT,
    )
    # A different ("v2") synthetic price gives a different cost — but the v1 value
    # recomputed from the v1 snapshot is unchanged, so a stored v1 snapshot is
    # stable across a price change.
    v2 = compute_cost_micros(
        input_tokens=1_000, output_tokens=1_000,
        input_price_micros_per_mtok=SYNTH_A_IN * 2, output_price_micros_per_mtok=SYNTH_A_OUT * 2,
    )
    v1_again = compute_cost_micros(
        input_tokens=1_000, output_tokens=1_000,
        input_price_micros_per_mtok=SYNTH_A_IN, output_price_micros_per_mtok=SYNTH_A_OUT,
    )
    assert v1 == v1_again
    assert v2 == 2 * v1
