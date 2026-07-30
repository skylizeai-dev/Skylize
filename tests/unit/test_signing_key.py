"""
Stable signing-key infrastructure (Sprint-2 Task 3).

Proves:
  - production (non-memory backend) FAILS CLOSED when no key is configured;
  - a configured PEM is loaded and used;
  - garbage / wrong-curve keys are rejected at startup;
  - two instances sharing the SAME key cross-validate tokens (multi-instance
    signing + validation), and two instances with DIFFERENT keys do NOT.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from skylize.app.governance.keys import SigningKeyError, load_signing_key
from skylize.config import Settings
from skylize.contracts.token import TokenSigner, verify_token_signature
from skylize.security.ecc_service import Curve, ECCService


def _p384_pem() -> str:
    return ECCService.generate_key_pair(Curve.P384).private_pem().decode()


def _prod(**kwargs: object) -> Settings:
    """A minimally-valid non-memory Settings for the signing-key branch.

    `backend="postgres"` is the only thing these cases care about — it selects
    `load_signing_key`'s fail-closed branch. The three extra fields satisfy the
    boot interlocks that guard that same backend: dev auth is refused on a real
    backend, turning it off requires a JWT secret, and the runtime DSN must be a
    distinct non-superuser role so RLS is not bypassed. None of them affects what
    is under test here.
    """
    return Settings(
        backend="postgres",
        dev_auth=False,
        jwt_secret="signing-key-test-secret-not-a-credential",
        db_app_url="postgresql://skylize_app@localhost:5432/skylize",
        **kwargs,  # type: ignore[arg-type]
    )


def test_production_without_key_fails_closed() -> None:
    settings = _prod(governance_signing_key_pem="")
    with pytest.raises(SigningKeyError, match="No governance signing key"):
        load_signing_key(settings)


def test_memory_backend_without_key_allows_ephemeral() -> None:
    settings = Settings(backend="memory", governance_signing_key_pem="")
    pair = load_signing_key(settings)  # must not raise
    assert pair.private_key.curve.key_size == 384


def test_configured_pem_is_loaded() -> None:
    pem = _p384_pem()
    for settings in (
        Settings(backend="memory", governance_signing_key_pem=pem),
        _prod(governance_signing_key_pem=pem),
    ):
        pair = load_signing_key(settings)
        assert pair.private_key.curve.key_size == 384


def test_garbage_pem_is_rejected() -> None:
    settings = _prod(governance_signing_key_pem="-----BEGIN nonsense-----")
    with pytest.raises(SigningKeyError, match="could not be parsed"):
        load_signing_key(settings)


def test_wrong_curve_key_is_rejected() -> None:
    # A P-256 key is not acceptable for the P-384 governance curve.
    p256_pem = ECCService.generate_key_pair(Curve.P256).private_pem().decode()
    settings = _prod(governance_signing_key_pem=p256_pem)
    with pytest.raises(SigningKeyError, match="P-384"):
        load_signing_key(settings)


def _mint_token(signer: TokenSigner):
    now = datetime.now(timezone.utc)
    return signer.sign(
        token_id=uuid4(), agent_id="hook_generator_agent", authority_level="worker",
        department="creative", delegation_chain=["hook_generator_agent"],
        scope=["llm.generate"], max_token_budget=8000, max_execution_time_seconds=60,
        issued_at=now, expires_at=now + timedelta(minutes=5), nonce=uuid4().hex,
    )


def test_same_key_cross_instance_validation() -> None:
    """A token minted on instance A validates on instance B when keys match."""
    pem = _p384_pem()
    a = load_signing_key(_prod(governance_signing_key_pem=pem))
    b = load_signing_key(_prod(governance_signing_key_pem=pem))

    token = _mint_token(TokenSigner(a.private_key))
    assert verify_token_signature(token, b.public_key) is True


def test_different_keys_do_not_cross_validate() -> None:
    """Distinct keys (the ephemeral-per-pod bug) must FAIL cross-validation."""
    a = load_signing_key(_prod(governance_signing_key_pem=_p384_pem()))
    b = load_signing_key(_prod(governance_signing_key_pem=_p384_pem()))

    token = _mint_token(TokenSigner(a.private_key))
    assert verify_token_signature(token, b.public_key) is False
