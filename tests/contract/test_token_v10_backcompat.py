"""The v1.0 backward-compatibility lock.

Every value below was produced by the code as it stood BEFORE `token_version`
existed, captured once and frozen. ECDSA signatures are randomized, so this
signature can never be regenerated -- it is only ever verified. That is exactly
what makes this test a lock rather than a tautology: if any future change alters
`canonical_signing_bytes` for a v1.0 token by so much as one byte, the hardcoded
signature stops verifying and this file fails.

The governing constraint it enforces:

    EVERY EXISTING TOKEN MUST STILL VERIFY, BIT-IDENTICALLY.

Do NOT regenerate these constants. If this test fails, the change under
development broke backward compatibility for every token already in flight.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from cryptography.hazmat.primitives import serialization

from skylize.contracts.base import GovernanceToken
from skylize.contracts.token import (
    canonical_signing_bytes,
    token_signing_bytes,
    verify_token_signature,
)

# --------------------------------------------------------------------------- #
# FROZEN FIXTURE -- captured from the pre-change code. Never regenerate.
# --------------------------------------------------------------------------- #

_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MHYwEAYHKoZIzj0CAQYFK4EEACIDYgAEi8sx/Dmcqepgj7qMlW0SIADQAZarN3oL
yOgu4kI6WQ7wWYynH3wNZshY1DveV8P4gTlHJHPhOGxwbtPsgZJYcLbJ8UcN7a4p
0dzF3jhg/1KAWDz78sHz/tDBluKsqC00
-----END PUBLIC KEY-----
"""

_SIGNATURE = (
    "MGYCMQDD7bF68pDhwXYEca_IAQXd1gF_i5jqG6jpGID_njZ0eJkDIL90pvsNja2fVxpk0i8"
    "CMQCZNXIyk6DKDlIREzc_CY5paPR0srxCnLNpCVjv148lOipA-aY5CAA1LH6j8-cWRN8"
)

# The exact bytes the pre-change signer signed over. 431 bytes, keys sorted
# alphabetically by json.dumps(sort_keys=True) -- note this is NOT the field
# declaration order, which is why adding a field to the model cannot by itself
# perturb the ordering of existing keys.
_SIGNING_BYTES = (
    b'{"agent_id":"hook_generator_agent","authority_level":"worker",'
    b'"delegation_chain":["vp_creative","copy_director","hook_generator_agent"],'
    b'"department":"creative","expires_at":"2026-01-15T09:05:00+00:00",'
    b'"issued_at":"2026-01-15T09:00:00+00:00","max_execution_time_seconds":60,'
    b'"max_token_budget":8000,"nonce":"0123456789abcdef0123456789abcdef",'
    b'"scope":["llm.generate","memory.search"],'
    b'"token_id":"11111111-2222-3333-4444-555555555555"}'
)

_TOKEN_ID = UUID("11111111-2222-3333-4444-555555555555")
_AGENT_ID = "hook_generator_agent"
_DELEGATION_CHAIN = ["vp_creative", "copy_director", "hook_generator_agent"]
_SCOPE = ["llm.generate", "memory.search"]
_ISSUED_AT = datetime(2026, 1, 15, 9, 0, 0, tzinfo=timezone.utc)
_EXPIRES_AT = datetime(2026, 1, 15, 9, 5, 0, tzinfo=timezone.utc)
_NONCE = "0123456789abcdef0123456789abcdef"


def _public_key():
    return serialization.load_pem_public_key(_PUBLIC_KEY_PEM)


def _legacy_token(**overrides) -> GovernanceToken:
    """The pre-change token, reconstructed exactly as a deserializing verifier
    would see it -- note that NO token_version is passed, so a token persisted
    before that field existed must default to the v1.0 canonicalization."""
    base = dict(
        token_id=_TOKEN_ID,
        agent_id=_AGENT_ID,
        authority_level="worker",
        department="creative",
        delegation_chain=list(_DELEGATION_CHAIN),
        scope=list(_SCOPE),
        max_token_budget=8000,
        max_execution_time_seconds=60,
        issued_at=_ISSUED_AT,
        expires_at=_EXPIRES_AT,
        nonce=_NONCE,
        signature=_SIGNATURE,
    )
    return GovernanceToken(**{**base, **overrides})


# --------------------------------------------------------------------------- #
# The lock
# --------------------------------------------------------------------------- #


def test_pre_change_v10_token_still_verifies() -> None:
    """THE non-negotiable gate. A token signed before this feature existed must
    still verify against the same public key, unchanged."""
    assert verify_token_signature(_legacy_token(), _public_key()) is True


def test_v10_signing_bytes_are_byte_identical() -> None:
    """Stronger than signature verification: the reconstructed bytes must equal
    the frozen bytes exactly, so a failure points at the serializer rather than
    at the crypto."""
    assert token_signing_bytes(_legacy_token()) == _SIGNING_BYTES
    assert len(_SIGNING_BYTES) == 431


def test_canonical_signing_bytes_default_is_v10() -> None:
    """Called with no version argument at all -- the shape every pre-existing
    caller uses -- canonical_signing_bytes must still emit the v1.0 payload."""
    body = canonical_signing_bytes(
        token_id=_TOKEN_ID,
        agent_id=_AGENT_ID,
        authority_level="worker",
        department="creative",
        delegation_chain=list(_DELEGATION_CHAIN),
        scope=list(_SCOPE),
        max_token_budget=8000,
        max_execution_time_seconds=60,
        issued_at=_ISSUED_AT,
        expires_at=_EXPIRES_AT,
        nonce=_NONCE,
    )
    assert body == _SIGNING_BYTES


def test_v10_payload_carries_no_version_key() -> None:
    """A v1.0 token's signed payload must contain NO token_version key -- not
    even as null. Emitting one would change the bytes of every legacy token."""
    assert b"token_version" not in token_signing_bytes(_legacy_token())


def test_legacy_token_defaults_to_v10() -> None:
    """A token deserialized from storage written before the field existed."""
    assert _legacy_token().token_version == "1.0"


def test_tampering_still_breaks_the_frozen_signature() -> None:
    """The lock must fail for the right reason -- prove it is actually checking
    the signature and not trivially passing."""
    forged = _legacy_token().model_copy(update={"max_token_budget": 999_999})
    assert verify_token_signature(forged, _public_key()) is False


# --------------------------------------------------------------------------- #
# Version dispatch -- the mechanism itself
# --------------------------------------------------------------------------- #


def _fresh_signer():
    from skylize.contracts.token import TokenSigner
    from skylize.security.ecc_service import Curve, ECCService

    pair = ECCService.generate_key_pair(Curve.P384)
    return TokenSigner(pair.private_key), pair.public_key


def _sign(signer, *, token_version="1.0"):
    return signer.sign(
        token_id=_TOKEN_ID,
        agent_id=_AGENT_ID,
        authority_level="worker",
        department="creative",
        delegation_chain=list(_DELEGATION_CHAIN),
        scope=list(_SCOPE),
        max_token_budget=8000,
        max_execution_time_seconds=60,
        issued_at=_ISSUED_AT,
        expires_at=_EXPIRES_AT,
        nonce=_NONCE,
        token_version=token_version,
    )


def test_v11_payload_carries_the_version_key() -> None:
    """v1.1 is a genuinely different payload -- otherwise the two versions would
    be indistinguishable to a verifier and the dispatch would be meaningless."""
    signer, _ = _fresh_signer()
    body = token_signing_bytes(_sign(signer, token_version="1.1"))
    assert b'"token_version":"1.1"' in body
    assert body != _SIGNING_BYTES


def test_v10_and_v11_bytes_differ_for_otherwise_identical_tokens() -> None:
    signer, _ = _fresh_signer()
    v10 = _sign(signer, token_version="1.0")
    v11 = _sign(signer, token_version="1.1")
    assert token_signing_bytes(v10) != token_signing_bytes(v11)


def test_both_versions_verify_under_their_own_signature() -> None:
    signer, pub = _fresh_signer()
    assert verify_token_signature(_sign(signer, token_version="1.0"), pub) is True
    assert verify_token_signature(_sign(signer, token_version="1.1"), pub) is True


def test_downgrade_v11_to_v10_breaks_the_signature() -> None:
    """ATTACK: relabel a legitimately-signed v1.1 token as v1.0 to shed whatever
    the newer version binds. The verifier reconstructs the v1.0 payload, which
    is not what was signed, so the signature fails."""
    signer, pub = _fresh_signer()
    downgraded = _sign(signer, token_version="1.1").model_copy(
        update={"token_version": "1.0"}
    )
    assert verify_token_signature(downgraded, pub) is False


def test_promotion_v10_to_v11_breaks_the_signature() -> None:
    """ATTACK: relabel a legitimately-signed v1.0 token as v1.1 so it is read
    under the newer rules. Same defence, opposite direction."""
    signer, pub = _fresh_signer()
    promoted = _sign(signer, token_version="1.0").model_copy(
        update={"token_version": "1.1"}
    )
    assert verify_token_signature(promoted, pub) is False


def test_frozen_legacy_token_cannot_be_promoted() -> None:
    """The same promotion attack against the real frozen pre-change token."""
    promoted = _legacy_token().model_copy(update={"token_version": "1.1"})
    assert verify_token_signature(promoted, _public_key()) is False
