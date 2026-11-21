"""Stripe test-mode configuration (owner ruling OD8): default off, test keys only.

OD8 (docs/decisions/2026-11-07_owner_rulings.md:58) reads "Stripe: wire in test
mode behind a flag, off in production". Settings has no environment field, so
"off in production" is enforced as "live keys are refused at boot". These tests
pin the four refused combinations, the two accepted ones, and that the key value
never reaches repr / str / JSON dumps / ValidationError text.

Only obviously fake keys appear here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from skylize.config import STRIPE_TEST_KEY_PREFIXES, Settings, stripe_config_problem

_TEST_KEY = "sk_test_dummy"
_RESTRICTED_TEST_KEY = "rk_test_dummy"
_LIVE_KEY = "sk_live_dummy_not_a_real_key_0000"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The machine's own environment must not decide these results.
    monkeypatch.delenv("SKYLIZE_STRIPE_ENABLED", raising=False)
    monkeypatch.delenv("SKYLIZE_STRIPE_SECRET_KEY", raising=False)


def _settings(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg, arg-type]


# -- defaults ----------------------------------------------------------------

def test_stripe_is_off_by_default() -> None:
    s = _settings()
    assert s.stripe_enabled is False
    assert s.stripe_secret_key.get_secret_value() == ""


def test_test_key_prefixes_are_test_mode_only() -> None:
    assert STRIPE_TEST_KEY_PREFIXES == ("sk_test_", "rk_test_")


# -- refused combinations ----------------------------------------------------

def test_flag_on_without_key_is_refused() -> None:
    with pytest.raises(ValidationError, match="SKYLIZE_STRIPE_SECRET_KEY"):
        _settings(stripe_enabled=True)


def test_whitespace_key_counts_as_no_key() -> None:
    with pytest.raises(ValidationError, match="SKYLIZE_STRIPE_SECRET_KEY is not"):
        _settings(stripe_enabled=True, stripe_secret_key="   ")
    # ...and with the flag off it is simply "off", not a half-set pair.
    assert _settings(stripe_secret_key="   ").stripe_enabled is False


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize(
    "key", [_LIVE_KEY, "rk_live_dummy_not_a_real_key_0000", "not_a_stripe_key_dummy"]
)
def test_non_test_key_is_refused_with_flag_on_or_off(enabled: bool, key: str) -> None:
    with pytest.raises(ValidationError, match="TEST-mode"):
        _settings(stripe_enabled=enabled, stripe_secret_key=key)


def test_test_key_with_flag_off_is_refused_as_half_set() -> None:
    with pytest.raises(ValidationError, match="SKYLIZE_STRIPE_ENABLED is not"):
        _settings(stripe_secret_key=_TEST_KEY)


# -- accepted ----------------------------------------------------------------

@pytest.mark.parametrize("key", [_TEST_KEY, _RESTRICTED_TEST_KEY])
def test_test_key_with_flag_on_is_accepted(key: str) -> None:
    s = _settings(stripe_enabled=True, stripe_secret_key=key)
    assert s.stripe_enabled is True
    assert s.stripe_secret_key.get_secret_value() == key


def test_env_var_path_enables_stripe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKYLIZE_STRIPE_ENABLED", "true")
    monkeypatch.setenv("SKYLIZE_STRIPE_SECRET_KEY", _TEST_KEY)
    s = _settings()
    assert s.stripe_enabled is True
    assert s.stripe_secret_key.get_secret_value() == _TEST_KEY


# -- the key never leaks -----------------------------------------------------

def test_key_value_not_in_repr_str_or_json() -> None:
    s = _settings(stripe_enabled=True, stripe_secret_key=_TEST_KEY)
    assert _TEST_KEY not in repr(s)
    assert _TEST_KEY not in str(s)
    assert _TEST_KEY not in s.model_dump_json()


def test_refused_key_not_in_validation_error_kwargs() -> None:
    with pytest.raises(ValidationError) as ei:
        _settings(stripe_enabled=True, stripe_secret_key=_LIVE_KEY)
    # pydantic otherwise echoes `input_value=...` - check a distinctive tail too,
    # because pydantic truncates long inputs from the middle.
    for text in (str(ei.value), repr(ei.value)):
        assert _LIVE_KEY not in text
        assert "real_key_0000" not in text


def test_refused_key_not_in_validation_error_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKYLIZE_STRIPE_ENABLED", "true")
    monkeypatch.setenv("SKYLIZE_STRIPE_SECRET_KEY", _LIVE_KEY)
    with pytest.raises(ValidationError) as ei:
        _settings()
    for text in (str(ei.value), repr(ei.value)):
        assert _LIVE_KEY not in text
        assert "real_key_0000" not in text


# -- the pure helper ---------------------------------------------------------

@pytest.mark.parametrize(
    ("enabled", "key", "refused"),
    [
        (False, "", False),
        (False, "  ", False),
        (True, _TEST_KEY, False),
        (True, _RESTRICTED_TEST_KEY, False),
        (True, f"  {_TEST_KEY}  ", False),
        (True, "", True),
        (False, _TEST_KEY, True),
        (True, _LIVE_KEY, True),
        (False, _LIVE_KEY, True),
    ],
)
def test_stripe_config_problem_table(enabled: bool, key: str, refused: bool) -> None:
    problem = stripe_config_problem(enabled=enabled, secret_key=key)
    assert (problem is not None) is refused
    if problem is not None:
        assert "SKYLIZE_STRIPE_" in problem
        if key.strip():
            assert key.strip() not in problem
