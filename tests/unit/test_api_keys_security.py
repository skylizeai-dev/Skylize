"""API-key crypto primitives: generate/parse/verify roundtrip + rejections."""

from __future__ import annotations

from skylize.security.api_keys import (
    generate_api_key,
    hash_secret,
    parse_api_key,
    verify_secret,
)


def test_generate_parse_verify_roundtrip() -> None:
    g = generate_api_key()
    assert g.full_key.startswith("sky.")
    parsed = parse_api_key(g.full_key)
    assert parsed is not None
    prefix, secret = parsed
    assert prefix == g.prefix
    assert verify_secret(secret, g.key_hash)


def test_wrong_secret_rejected() -> None:
    g = generate_api_key()
    assert not verify_secret("not-the-secret", g.key_hash)


def test_only_the_hash_is_persistable() -> None:
    g = generate_api_key()
    parsed = parse_api_key(g.full_key)
    assert parsed is not None
    _, secret = parsed
    assert g.key_hash != secret  # never store the plaintext
    assert g.key_hash == hash_secret(secret)
    assert len(g.key_hash) == 64  # sha-256 hex


def test_parse_rejects_malformed() -> None:
    assert parse_api_key("garbage") is None
    assert parse_api_key("sky.onlytwo") is None
    assert parse_api_key("wrong.prefix.secret") is None
    assert parse_api_key("sky..emptyprefix") is None
