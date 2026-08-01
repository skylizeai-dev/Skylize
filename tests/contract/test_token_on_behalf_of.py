"""The v1.1 human-principal claim, and the ways it must refuse to be abused.

Companion to test_token_v10_backcompat.py, which locks the v1.0 bytes. This file
covers the claim itself: that it is genuinely bound by the signature, and that
every self-contradictory combination of version and claim is refused rather than
signed, crashed on, or silently accepted.

Several of these cases came out of an adversarial review of the design and would
NOT have been caught by the happy path:

  * pydantic's `model_copy` does not re-run validators, so a token whose version
    and claim disagree IS constructible at runtime. The verifier must answer
    `False` for it, not raise AttributeError while dereferencing a None claim.
  * `TokenSigner.sign` computes the signature from the canonical bytes, so the
    version/claim check has to happen during canonicalization -- before the
    private key is touched. Otherwise a rejected token still burns a real
    signature over a payload asserting a principal binding it does not carry.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest

from skylize.contracts.base import GovernanceToken, OnBehalfOf
from skylize.contracts.token import (
    MalformedToken,
    TokenSigner,
    canonical_signing_bytes,
    token_signing_bytes,
    verify_token_signature,
)
from skylize.security.ecc_service import Curve, ECCService

_ISSUED_AT = datetime(2026, 1, 15, 9, 0, 0, tzinfo=timezone.utc)
_EXPIRES_AT = datetime(2026, 1, 15, 9, 5, 0, tzinfo=timezone.utc)

CLAIM = OnBehalfOf(
    principal_id="devon",
    authority_fingerprint="a" * 64,
    session_kind="cowork",
)


def _signer_and_pub():
    pair = ECCService.generate_key_pair(Curve.P384)
    return TokenSigner(pair.private_key), pair.public_key


def _sign(signer, *, token_version="1.1", on_behalf_of=CLAIM):
    return signer.sign(
        token_id=uuid4(),
        agent_id="hook_generator_agent",
        authority_level="worker",
        department="creative",
        delegation_chain=["vp_creative", "hook_generator_agent"],
        scope=["llm.generate"],
        max_token_budget=8000,
        max_execution_time_seconds=60,
        issued_at=_ISSUED_AT,
        expires_at=_EXPIRES_AT,
        nonce=uuid4().hex,
        token_version=token_version,
        on_behalf_of=on_behalf_of,
    )


# --------------------------------------------------------------------------- #
# The claim is real and is bound by the signature
# --------------------------------------------------------------------------- #


def test_v11_token_with_claim_verifies() -> None:
    signer, pub = _signer_and_pub()
    token = _sign(signer)
    assert token.token_version == "1.1"
    assert token.on_behalf_of == CLAIM
    assert verify_token_signature(token, pub) is True


def test_claim_appears_in_the_signed_bytes() -> None:
    signer, _ = _signer_and_pub()
    body = token_signing_bytes(_sign(signer))
    assert b'"on_behalf_of"' in body
    assert b'"principal_id":"devon"' in body
    assert b'"authority_fingerprint"' in body
    assert b'"session_kind":"cowork"' in body


@pytest.mark.parametrize(
    "field, value",
    [
        ("principal_id", "someone_else"),
        ("authority_fingerprint", "b" * 64),
        ("session_kind", "autonomous"),
    ],
)
def test_tampering_with_any_claim_field_breaks_the_signature(field, value) -> None:
    """Every field of the claim must be inside the signature, not merely beside
    it -- otherwise an attacker swaps the principal and keeps the signature."""
    signer, pub = _signer_and_pub()
    token = _sign(signer)
    forged = token.model_copy(
        update={"on_behalf_of": token.on_behalf_of.model_copy(update={field: value})}
    )
    assert verify_token_signature(forged, pub) is False


def test_stripping_the_claim_entirely_breaks_the_signature() -> None:
    signer, pub = _signer_and_pub()
    stripped = _sign(signer).model_copy(update={"on_behalf_of": None})
    assert verify_token_signature(stripped, pub) is False


# --------------------------------------------------------------------------- #
# Self-contradictory tokens are refused, never signed and never crashed on
# --------------------------------------------------------------------------- #


def test_signing_v11_without_a_claim_raises() -> None:
    signer, _ = _signer_and_pub()
    with pytest.raises(MalformedToken, match="requires an on_behalf_of claim"):
        _sign(signer, token_version="1.1", on_behalf_of=None)


def test_signing_v11_without_a_claim_burns_no_signature() -> None:
    """THE ordering property: the refusal must happen during canonicalization,
    before the private key is used. If the check ran after signing, a real
    signature would exist over bytes claiming a principal binding that is not
    there -- and anything that captured it could replay it."""
    signer, _ = _signer_and_pub()
    with patch.object(
        ECCService, "sign_governance_token", wraps=ECCService.sign_governance_token
    ) as spy:
        with pytest.raises(MalformedToken):
            _sign(signer, token_version="1.1", on_behalf_of=None)
        spy.assert_not_called()


def test_signing_v10_with_a_claim_raises() -> None:
    """The v1.0 payload is frozen and cannot bind a claim, so quietly dropping
    one would issue a token that looks principal-bound but is not."""
    signer, _ = _signer_and_pub()
    with pytest.raises(MalformedToken, match="cannot be carried by a v1.0 token"):
        _sign(signer, token_version="1.0", on_behalf_of=CLAIM)


def test_canonical_signing_bytes_refuses_a_mismatch_directly() -> None:
    kwargs = dict(
        token_id=uuid4(),
        agent_id="a",
        authority_level="worker",
        department="creative",
        delegation_chain=["a"],
        scope=["llm.generate"],
        max_token_budget=10,
        max_execution_time_seconds=10,
        issued_at=_ISSUED_AT,
        expires_at=_EXPIRES_AT,
        nonce="n",
    )
    with pytest.raises(MalformedToken):
        canonical_signing_bytes(**kwargs, token_version="1.1", on_behalf_of=None)
    with pytest.raises(MalformedToken):
        canonical_signing_bytes(**kwargs, token_version="1.0", on_behalf_of=CLAIM)


# --------------------------------------------------------------------------- #
# The model validator, and the model_copy hole it cannot close
# --------------------------------------------------------------------------- #


def _token_kwargs(**overrides):
    base = dict(
        token_id=uuid4(),
        agent_id="a",
        authority_level="worker",
        department="creative",
        delegation_chain=["a"],
        scope=["llm.generate"],
        max_token_budget=10,
        max_execution_time_seconds=10,
        issued_at=_ISSUED_AT,
        expires_at=_EXPIRES_AT,
        nonce="n",
        signature="sig",
    )
    return {**base, **overrides}


def test_validator_rejects_v11_without_claim() -> None:
    with pytest.raises(ValueError, match="requires an on_behalf_of claim"):
        GovernanceToken(**_token_kwargs(token_version="1.1"))


def test_validator_rejects_v10_with_claim() -> None:
    with pytest.raises(ValueError, match="only carried by v1.1"):
        GovernanceToken(**_token_kwargs(on_behalf_of=CLAIM))


def test_model_copy_promotion_verifies_false_rather_than_crashing() -> None:
    """model_copy skips validators, so this token IS constructible with
    version="1.1" and no claim. Canonicalizing it must refuse cleanly and the
    verifier must report a plain False -- not raise AttributeError on None."""
    signer, pub = _signer_and_pub()
    promoted = _sign(signer, token_version="1.0", on_behalf_of=None).model_copy(
        update={"token_version": "1.1"}
    )
    assert promoted.on_behalf_of is None  # the validator really was bypassed
    assert verify_token_signature(promoted, pub) is False


def test_model_copy_downgrade_verifies_false_rather_than_crashing() -> None:
    signer, pub = _signer_and_pub()
    downgraded = _sign(signer).model_copy(update={"token_version": "1.0"})
    assert downgraded.on_behalf_of is not None  # validator bypassed again
    assert verify_token_signature(downgraded, pub) is False


def test_autonomous_path_is_untouched_by_the_claim() -> None:
    """A v1.0 token still signs, verifies, and carries no claim -- the shape
    every existing caller mints."""
    signer, pub = _signer_and_pub()
    token = _sign(signer, token_version="1.0", on_behalf_of=None)
    assert token.on_behalf_of is None
    assert token.token_version == "1.0"
    assert verify_token_signature(token, pub) is True
    assert b"on_behalf_of" not in token_signing_bytes(token)
    assert b"token_version" not in token_signing_bytes(token)
