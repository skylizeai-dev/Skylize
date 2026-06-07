"""
API-key crypto primitives (pure stdlib — no driver, no skylize imports).

A key is rendered as ``sky.<prefix>.<secret>``:
  - ``prefix`` (12 hex chars) is public and indexed — the O(1) lookup handle;
  - ``secret`` is confidential; only its SHA-256 hash is ever persisted.

``.`` is the structural delimiter precisely because neither a hex prefix nor a
url-safe secret can contain it, so parsing is unambiguous. Verification uses a
constant-time compare so a stored-hash timing oracle cannot leak the secret.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

_SCHEME = "sky"
_PREFIX_BYTES = 6  # -> 12 hex chars
_SECRET_BYTES = 32  # -> ~43 url-safe chars


@dataclass(frozen=True, slots=True)
class GeneratedKey:
    """The product of minting one key. ``full_key`` is shown to the caller once;
    only ``key_hash`` (and the public ``prefix``) is persisted."""

    prefix: str
    full_key: str
    key_hash: str


def hash_secret(secret: str) -> str:
    """SHA-256 hex of the secret component — this is what lands in the DB."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def verify_secret(secret: str, key_hash: str) -> bool:
    """Constant-time comparison of a presented secret against a stored hash."""
    return hmac.compare_digest(hash_secret(secret), key_hash)


def generate_api_key() -> GeneratedKey:
    """Mint a fresh key. The plaintext exists only in the returned ``full_key``."""
    prefix = secrets.token_hex(_PREFIX_BYTES)
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    return GeneratedKey(
        prefix=prefix,
        full_key=f"{_SCHEME}.{prefix}.{secret}",
        key_hash=hash_secret(secret),
    )


def parse_api_key(presented: str) -> tuple[str, str] | None:
    """Split ``sky.<prefix>.<secret>`` into ``(prefix, secret)``; None if malformed."""
    parts = presented.split(".")
    if len(parts) != 3 or parts[0] != _SCHEME or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]
