"""Unit tests for memory dedup primitives: canonicalization, hashing, decay."""

from __future__ import annotations

import unicodedata

import pytest

from skylize.memory.dedup import (
    DEFAULT_HALF_LIFE_SECONDS,
    DEFAULT_REINFORCEMENT,
    canonicalize_content,
    compute_fact_hash,
    decay_fn,
)


# ---- canonicalization ------------------------------------------------------

def test_canonicalize_lowercases_and_collapses_whitespace() -> None:
    assert canonicalize_content("  Hello   WORLD  ") == "hello world"


def test_canonicalize_collapses_all_whitespace_kinds() -> None:
    assert canonicalize_content("a\t\n  b\r\nc") == "a b c"


def test_canonicalize_is_idempotent() -> None:
    once = canonicalize_content("  MiXeD   Case\tText ")
    assert canonicalize_content(once) == once


def test_canonicalize_applies_nfc() -> None:
    # 'e' + combining acute (NFD) must normalize to the single composed 'é' (NFC).
    nfd = "Café"
    out = canonicalize_content(nfd)
    assert out == unicodedata.normalize("NFC", "café").lower()
    assert "́" not in out  # combining mark folded away


# ---- fact hash -------------------------------------------------------------

def test_hash_is_64_hex_chars() -> None:
    h = compute_fact_hash("creative", "some fact")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_stable_across_trivial_variation() -> None:
    a = compute_fact_hash("creative", "Hello World")
    b = compute_fact_hash("  CREATIVE ", "  hello   world ")
    assert a == b  # casing + whitespace + namespace casing do not fork identity


def test_hash_differs_by_namespace() -> None:
    a = compute_fact_hash("creative", "same content")
    b = compute_fact_hash("sales", "same content")
    assert a != b  # namespace is bound into identity


def test_hash_differs_by_content() -> None:
    a = compute_fact_hash("creative", "content one")
    b = compute_fact_hash("creative", "content two")
    assert a != b


def test_nfd_and_nfc_hash_identically() -> None:
    assert compute_fact_hash("ns", "Café") == compute_fact_hash("ns", "café")


# ---- decay -----------------------------------------------------------------

def test_decay_zero_elapsed_is_prior_plus_reinforcement() -> None:
    assert decay_fn(3.0, 0.0, reinforcement=1.0) == pytest.approx(4.0)


def test_decay_one_half_life_halves_prior_contribution() -> None:
    out = decay_fn(2.0, DEFAULT_HALF_LIFE_SECONDS, reinforcement=0.0)
    assert out == pytest.approx(1.0)  # 2 * 0.5^1 + 0


def test_decay_is_monotonic_decreasing_in_elapsed() -> None:
    base = decay_fn(5.0, 0.0)
    later = decay_fn(5.0, DEFAULT_HALF_LIFE_SECONDS)
    much_later = decay_fn(5.0, 10 * DEFAULT_HALF_LIFE_SECONDS)
    assert base > later > much_later


def test_decay_approaches_reinforcement_floor() -> None:
    out = decay_fn(100.0, 1000 * DEFAULT_HALF_LIFE_SECONDS, reinforcement=1.0)
    assert out == pytest.approx(DEFAULT_REINFORCEMENT, abs=1e-6)
    assert out >= DEFAULT_REINFORCEMENT


def test_decay_rejects_negative_elapsed() -> None:
    with pytest.raises(ValueError):
        decay_fn(1.0, -1.0)


def test_decay_rejects_nonpositive_half_life() -> None:
    with pytest.raises(ValueError):
        decay_fn(1.0, 1.0, half_life_seconds=0.0)
