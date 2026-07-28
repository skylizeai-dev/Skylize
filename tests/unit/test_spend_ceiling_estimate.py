"""Unit tests for the pending-call spend estimate (Phase 2, steps 5-9).

Pure functions, no DB: they pin the DIRECTION OF ERROR (the estimate must err
HIGH), the MICRO-USD unit (owner decision D3), and the rounding direction
(toward +infinity, so a sub-micro cost never rounds down to zero).
"""

from __future__ import annotations

import math

from skylize.adapters.llm.spend_ceiling import (
    _CHARS_PER_TOKEN_ESTIMATE,
    _INPUT_TOKEN_SAFETY_MULTIPLIER,
    estimate_input_tokens,
    estimate_max_micros,
)


def test_heuristic_constants_are_biased_high() -> None:
    """The named module-level constants (no magic numbers inline, step 7) are set
    so the token estimate errs HIGH: a smaller chars/token divisor over-counts
    tokens, and a >1 multiplier adds headroom."""
    # ~4 chars/token is the real English average; using a smaller divisor yields
    # MORE estimated tokens (the safe, over-counting direction).
    assert _CHARS_PER_TOKEN_ESTIMATE < 4.0
    assert _INPUT_TOKEN_SAFETY_MULTIPLIER > 1.0


def test_estimate_input_tokens_over_counts_vs_naive_four_per_token() -> None:
    """For any non-trivial input, the estimate exceeds a naive 4-chars/token count
    — over-estimating input tokens is the safe direction for a spend gate."""
    for chars in (12, 100, 400, 4000, 40_000):
        naive_four = math.ceil(chars / 4)
        assert estimate_input_tokens(chars) > naive_four, chars


def test_estimate_input_tokens_zero_and_monotonic() -> None:
    assert estimate_input_tokens(0) == 0
    assert estimate_input_tokens(-5) == 0  # defensive: never negative
    # More characters can never yield fewer estimated tokens.
    prev = -1
    for chars in range(0, 2000, 37):
        cur = estimate_input_tokens(chars)
        assert cur >= prev
        prev = cur


def test_estimate_max_micros_is_micro_usd() -> None:
    """D3: the estimate is in MICRO-USD. 1,000,000 output tokens priced at
    15,000,000 micro-USD/Mtok is exactly $15 == 15,000,000 micro-USD."""
    micros = estimate_max_micros(
        input_chars=0,  # isolate the output term
        requested_max_tokens=1_000_000,
        input_price_micros_per_mtok=0,
        output_price_micros_per_mtok=15_000_000,  # $15 / Mtok
    )
    assert micros == 15_000_000  # == $15.00 in micro-USD


def test_estimate_max_micros_counts_both_input_and_output() -> None:
    """estimated = est_input_tokens * in_rate + requested_max_tokens * out_rate,
    all in micro-USD. At 1 micro-USD/token each, the total is the token sum."""
    # 1_000_000 micro-USD / Mtok == 1 micro-USD / token.
    est_input = estimate_input_tokens(300)
    micros = estimate_max_micros(
        input_chars=300,
        requested_max_tokens=500,
        input_price_micros_per_mtok=1_000_000,
        output_price_micros_per_mtok=1_000_000,
    )
    assert micros == est_input + 500


def test_estimate_max_micros_rounds_up_never_down() -> None:
    """A sub-micro estimated cost rounds UP to 1 micro-USD (ROUND_CEILING), never
    truncates to 0 — truncating would be the unsafe direction."""
    micros = estimate_max_micros(
        input_chars=1,
        requested_max_tokens=1,
        input_price_micros_per_mtok=1,  # 1 micro-USD / Mtok => 1e-6 micro-USD/token
        output_price_micros_per_mtok=1,
    )
    # True value is a tiny fraction of one micro; it must round UP to 1, not 0.
    assert micros == 1


def test_estimate_max_micros_output_pinned_to_requested_max() -> None:
    """Output tokens are pinned to requested_max_tokens (the most a call can
    produce), so raising the cap raises the estimate — monotonic in the cap."""
    base = estimate_max_micros(
        input_chars=100,
        requested_max_tokens=1_000,
        input_price_micros_per_mtok=3_000_000,
        output_price_micros_per_mtok=15_000_000,
    )
    bigger = estimate_max_micros(
        input_chars=100,
        requested_max_tokens=10_000,
        input_price_micros_per_mtok=3_000_000,
        output_price_micros_per_mtok=15_000_000,
    )
    assert bigger > base
