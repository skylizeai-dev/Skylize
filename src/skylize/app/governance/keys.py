"""
Governance signing-key loading and validation (Sprint-2 Task 3).

The Governance Authority's ECDSA P-384 private key is the single root of trust:
every governance token is signed with it and validated against the matching
public key on every instance. For that to work across replicas the key MUST be
stable and shared — an ephemeral per-process key makes a token minted on pod A
fail verification on pod B (and silently breaks multi-instance governance).

Policy:
  - production (`backend != "memory"`): a signing key is REQUIRED. If it is
    missing or unparseable, startup FAILS CLOSED — we never fall back to an
    ephemeral key in production.
  - dev/local (`backend == "memory"`): an ephemeral key is allowed for
    convenience, but only there, and it is logged loudly.
"""

from __future__ import annotations

import logging

from ...config import Settings
from ...contracts.token import GOVERNANCE_CURVE
from ...security.ecc_service import ECCService, ECKeyPair

log = logging.getLogger("skylize.governance.keys")


class SigningKeyError(RuntimeError):
    """Raised when a required governance signing key is missing or invalid."""


def load_signing_key(settings: Settings) -> ECKeyPair:
    """Return the Authority key pair, failing closed in production.

    Resolution order:
      1. `governance_signing_key_pem` (inline PEM, e.g. from a secrets manager).
      2. (production only) error — no key, no start.
      3. (memory backend only) generate an ephemeral P-384 key.
    """
    pem = settings.governance_signing_key_pem.strip()
    if pem:
        try:
            pair = ECCService.load_private_key_pem(
                pem.encode(), curve=GOVERNANCE_CURVE
            )
        except Exception as exc:  # noqa: BLE001 — any parse failure is fatal
            raise SigningKeyError(
                f"governance_signing_key_pem is set but could not be parsed: {exc}"
            ) from exc
        _assert_p384(pair)
        return pair

    # No key configured.
    if settings.backend != "memory":
        raise SigningKeyError(
            "No governance signing key configured. Set SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM "
            "(production must NOT use an ephemeral key — it breaks multi-instance "
            "token verification). Refusing to start."
        )

    log.warning(
        "No governance signing key configured; generating an EPHEMERAL P-384 key. "
        "This is allowed only for the in-memory/dev backend and must never be used "
        "in production."
    )
    return ECCService.generate_key_pair(GOVERNANCE_CURVE)


def _assert_p384(pair: ECKeyPair) -> None:
    """The governance curve is fixed at P-384; reject anything else."""
    key_size = pair.private_key.curve.key_size
    if key_size != 384:
        raise SigningKeyError(
            f"governance signing key must be P-384 (got curve key_size={key_size})"
        )
