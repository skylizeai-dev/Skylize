"""
Verify that sign_payload produces a signature the server's _verify_hmac accepts.
The server logic is replicated here (not imported) to avoid crossing the
src/skylize boundary.
"""
from __future__ import annotations

import hashlib
import hmac

import pytest

from anytype_sync.ingest_client import sign_payload


# Replicate server _verify_hmac exactly (knowledge.py lines 24-28).
def _server_verify(body: bytes, signature_header: str | None, secret: str) -> bool:
    if not signature_header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


_SECRET = "test-hmac-secret"
_BODY = b'{"doc_id": "abc", "content": "hello", "source_path": "anytype://s/abc"}'


def test_signature_format() -> None:
    sig = sign_payload(_BODY, _SECRET)
    assert sig.startswith("sha256=")
    hex_part = sig[len("sha256="):]
    assert len(hex_part) == 64
    assert all(c in "0123456789abcdef" for c in hex_part)


def test_signature_accepted_by_server() -> None:
    sig = sign_payload(_BODY, _SECRET)
    assert _server_verify(_BODY, sig, _SECRET)


def test_wrong_secret_rejected() -> None:
    sig = sign_payload(_BODY, _SECRET)
    assert not _server_verify(_BODY, sig, "wrong-secret")


def test_tampered_body_rejected() -> None:
    sig = sign_payload(_BODY, _SECRET)
    tampered = _BODY + b" extra"
    assert not _server_verify(tampered, sig, _SECRET)


def test_missing_signature_rejected() -> None:
    assert not _server_verify(_BODY, None, _SECRET)


def test_empty_prefix_rejected() -> None:
    # A bare hex digest without "sha256=" prefix must fail
    bare = hmac.new(_SECRET.encode(), _BODY, hashlib.sha256).hexdigest()
    assert not _server_verify(_BODY, bare, _SECRET)


@pytest.mark.parametrize("secret", ["s", "a" * 64, "unicode-ñ-key"])
def test_various_secrets(secret: str) -> None:
    body = b"payload"
    sig = sign_payload(body, secret)
    assert _server_verify(body, sig, secret)
