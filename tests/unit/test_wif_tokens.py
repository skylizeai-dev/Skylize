"""Minting the federation assertion.

The strongest test here is `test_token_verifies_against_the_published_jwks`: it
signs with the private key and verifies with nothing but the PUBLIC document this
platform serves to Google. If the signature format, the `kid`, the algorithm, or
the JWK encoding were wrong, that round trip is where it shows — and it is
exactly the operation Google performs.
"""

from __future__ import annotations

import base64
import time

import pytest

from skylize.app.gcp.keys import load_wif_signing_key
from skylize.app.gcp.oidc import SLUG_LENGTH, issuer_url, jwks_document
from skylize.app.gcp.tokens import (
    CLOCK_SKEW_LEEWAY,
    MAX_SUBJECT_BYTES,
    PURPOSE_COMPUTE_STOP,
    TOKEN_LIFETIME,
    WifTokenError,
    assert_subject_fits,
    mint_id_token,
    subject_for,
)
from skylize.config import Settings

BASE = "https://oidc.example.com"
SLUG = "q" * SLUG_LENGTH
AUD = (
    "//iam.googleapis.com/projects/123456789/locations/global/"
    "workloadIdentityPools/skylize-pool/providers/skylize-provider"
)


def _key():
    key = load_wif_signing_key(Settings(backend="memory", wif_issuer_base_url=BASE))
    assert key is not None
    return key


def _mint(org_id: str = "org_acme", **kw) -> str:
    return mint_id_token(
        key=_key(),
        issuer=issuer_url(BASE, SLUG),
        org_id=org_id,
        audience=AUD,
        environment="production",
        **kw,
    )


def _segment(token: str, index: int) -> bytes:
    part = token.split(".")[index]
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


# ---------------------------------------------------------------------------
# THE ROUND TRIP — sign privately, verify from the public document only
# ---------------------------------------------------------------------------

def test_token_verifies_against_the_published_jwks() -> None:
    """Exactly what Google does: fetch the JWKS, verify the assertion.

    Nothing private is used on the verification side — only `jwks_document`,
    which is byte-for-byte what the public endpoint serves.
    """
    from jose import jwt as jose_jwt

    key = _key()
    token = mint_id_token(
        key=key,
        issuer=issuer_url(BASE, SLUG),
        org_id="org_acme",
        audience=AUD,
        environment="production",
    )
    claims = jose_jwt.decode(
        token,
        jwks_document(key),          # PUBLIC material only
        algorithms=["ES256"],
        audience=AUD,
        issuer=issuer_url(BASE, SLUG),
    )
    assert claims["sub"] == "org:org_acme:killswitch"
    assert claims["skylize_org_id"] == "org_acme"
    assert claims["skylize_purpose"] == PURPOSE_COMPUTE_STOP
    assert claims["skylize_env"] == "production"


def test_signature_is_raw_r_s_and_not_der() -> None:
    """JWS requires raw R||S (64 bytes on P-256); `ECCService.sign` emits DER.

    A DER signature here would be rejected by Google with an opaque
    `invalid_grant` that looks like a customer configuration fault. This pins the
    format so a refactor toward `ECCService.sign` fails here instead of in
    production.
    """
    signature = _segment(_mint(), 2)
    assert len(signature) == 64, (
        f"expected 64-byte raw R||S, got {len(signature)} bytes "
        "(70-72 would mean a DER signature)"
    )


def test_header_carries_the_kid_and_algorithm() -> None:
    """Without a `kid` a verifier must try every key in the set; with the wrong
    one it fails outright. A rotation pass depends on this being present now."""
    import json

    header = json.loads(_segment(_mint(), 0))
    assert header["alg"] == "ES256"
    assert header["kid"] == _key().key_id


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------

def test_lifetime_is_five_minutes_not_googles_sixty_minute_ceiling() -> None:
    import json

    claims = json.loads(_segment(_mint(), 1))
    assert claims["exp"] - claims["iat"] == int(TOKEN_LIFETIME.total_seconds())
    assert claims["exp"] - claims["iat"] == 300


def test_nbf_absorbs_clock_skew_but_exp_does_not() -> None:
    """Being generous about when a token STARTS being valid is cheap; being
    generous about when it STOPS is the direction that costs security."""
    import json

    claims = json.loads(_segment(_mint(), 1))
    assert claims["iat"] - claims["nbf"] == int(CLOCK_SKEW_LEEWAY.total_seconds())
    assert claims["exp"] > claims["iat"]


def test_each_token_is_uniquely_identified() -> None:
    import json

    a = json.loads(_segment(_mint(), 1))["jti"]
    b = json.loads(_segment(_mint(), 1))["jti"]
    assert a != b


def test_audience_is_passed_through_verbatim_not_derived() -> None:
    """A customer may configure a custom allowed audience; a derived value would
    diverge silently and fail at the moment of use."""
    import json

    custom = "https://skylize.example/custom-audience"
    token = mint_id_token(
        key=_key(),
        issuer=issuer_url(BASE, SLUG),
        org_id="org_acme",
        audience=custom,
        environment="production",
    )
    assert json.loads(_segment(token, 1))["aud"] == custom


# ---------------------------------------------------------------------------
# The stable subject, and Google's 127-byte limit
# ---------------------------------------------------------------------------

def test_subject_is_stable_across_mints() -> None:
    """The customer binds an IAM role to `principal://.../subject/<sub>`.

    A subject that varied per call would be unbindable: a role cannot be granted
    to an unbounded set of principals.
    """
    import json

    a = json.loads(_segment(_mint(), 1))["sub"]
    b = json.loads(_segment(_mint(), 1))["sub"]
    assert a == b == subject_for("org_acme")


def test_oversized_org_id_is_refused_at_mint_and_available_to_onboarding() -> None:
    """Google maps `sub` into `google.subject`, limited to 127 bytes.

    Deterministic per org, so it belongs in onboarding validation — but it is
    also enforced at mint time so it can never reach Google as a runtime failure.
    """
    huge = "o" * 200
    with pytest.raises(WifTokenError, match="127"):
        assert_subject_fits(huge)
    with pytest.raises(WifTokenError, match="127"):
        _mint(org_id=huge)


def test_a_realistic_org_id_fits_comfortably() -> None:
    assert_subject_fits("org_" + "a" * 64)
    assert len(subject_for("org_" + "a" * 64).encode()) < MAX_SUBJECT_BYTES


def test_mint_failures_are_never_evidence_of_customer_revocation() -> None:
    """`WifTokenError` is always a Skylize-side fault. It is a distinct type so a
    caller cannot mistake it for a provider signal and write terminal state from
    it."""
    assert issubclass(WifTokenError, RuntimeError)
    with pytest.raises(WifTokenError):
        _mint(org_id="x" * 300)


def test_purpose_claim_is_a_wire_contract_constant() -> None:
    """The customer pins this exact string in their attribute condition, so
    changing it silently breaks every existing federation."""
    assert PURPOSE_COMPUTE_STOP == "killswitch.compute.stop"


def test_token_is_not_accepted_after_expiry() -> None:
    from jose import exceptions as jose_exc
    from jose import jwt as jose_jwt

    key = _key()
    from datetime import datetime, timedelta, timezone

    stale = datetime.now(timezone.utc) - timedelta(hours=2)
    token = mint_id_token(
        key=key,
        issuer=issuer_url(BASE, SLUG),
        org_id="org_acme",
        audience=AUD,
        environment="production",
        now=stale,
    )
    with pytest.raises(jose_exc.ExpiredSignatureError):
        jose_jwt.decode(
            token, jwks_document(key), algorithms=["ES256"], audience=AUD
        )
    assert time.time() > 0  # sanity: the clock is real, not frozen
