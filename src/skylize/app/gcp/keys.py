"""WIF issuer signing key: loading, validation, and PUBLIC-only JWK export.

This module is the deliberate twin of ``app/governance/keys.py`` — same custody
discipline, same fail-closed posture, same ephemeral-only-on-memory-backend
escape hatch — for a DIFFERENT key serving a DIFFERENT trust domain.

WHY IT IS A SEPARATE MODULE AND NOT A SECOND FUNCTION NEXT TO THE GOVERNANCE KEY
-------------------------------------------------------------------------------
The governance P-384 key signs an INTERNAL authority artifact that is validated
inside the platform. This key's PUBLIC half is published on an internet-facing
JWKS document, which invites Google's Security Token Service to accept anything
it signs, on behalf of a customer, against that customer's production
infrastructure. Those are two trust domains and they get two keys.

The separation is structural, not conventional. There is NO import edge between
this module and ``app.governance.keys`` in either direction, and neither loader
can return the other's material: they read different settings fields, assert
different curves, and return different types. A caller holding a
``WifSigningKey`` cannot reach the governance key through it, and vice versa.
``skylize.security.ecc_service`` is imported by both, but that is a shared
crypto LIBRARY, not a shared key-access path — it holds no key state.

The separation is also technically forced, which is worth recording because a
future reader may be tempted to "simplify" by merging them: the governance curve
is P-384, signing as ES384, and Google accepts ONLY ``RS256`` or ``ES256``
(docs.cloud.google.com/iam/docs/workload-identity-federation-with-other-providers,
verified 2026-09-04). The governance key is not a legal WIF signing key.

WHY ES256 (P-256) AND NOT RS256
-------------------------------
Google states no preference between the two (verified 2026-09-04 against both the
SaaS-vendor guide and the other-providers guide; neither expresses one). With the
choice genuinely free, ES256 wins on codebase fit: ``ECCService`` already
implements P-256 end to end — curve map, hash map, PKCS8 PEM serialisation, PEM
loading — so this key introduces no new crypto primitive, no new serialisation
path, and no new validation code. RS256 would make this the platform's first RSA
key, with all of that built from scratch, for no security or compatibility gain.

A FOOTGUN THIS MODULE EXISTS TO PREVENT
---------------------------------------
``ECCService.sign`` emits a DER-encoded ECDSA signature. JWS requires the raw
``R || S`` concatenation (RFC 7515), which for P-256 is exactly 64 bytes. A token
signed with ``ECCService.sign`` would be silently rejected by Google with an
opaque error. Signing therefore goes through ``jose.jwt.encode`` (see
``app/gcp/tokens.py``), never through ``ECCService.sign``, and
``tests/unit/test_wif_keys.py`` pins the 64-byte raw-signature property so a
future refactor cannot quietly reintroduce DER.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey

from ...config import Settings
from ...security.ecc_service import Curve, ECCService

log = logging.getLogger("skylize.gcp.keys")

#: The WIF issuer curve is fixed at P-256 and the algorithm at ES256. Both are
#: constants, not configuration: Google accepts only RS256/ES256, and a
#: deployment that could vary this would be able to produce tokens Google
#: silently rejects at the exact moment the connection is needed.
WIF_CURVE = Curve.P256
WIF_ALGORITHM = "ES256"

#: Expected `key_size` of the WIF curve. Named so the assertion below reads as an
#: intent check rather than a magic number, mirroring `_assert_p384` in
#: app/governance/keys.py.
_WIF_KEY_SIZE = 256


class WifSigningKeyError(RuntimeError):
    """Raised when a required WIF signing key is missing, unparseable, or on the
    wrong curve. Distinct from `SigningKeyError` (governance) on purpose: the two
    keys fail independently and an operator must never be sent to the wrong
    secret while debugging."""


@dataclass(frozen=True, slots=True)
class WifSigningKey:
    """The WIF issuer's signing key and the `kid` that names it in the JWKS.

    Holds ONLY WIF material. It is deliberately not interchangeable with
    `ECKeyPair` (which the governance path uses) so a key of one kind cannot be
    passed where the other is expected and type-check clean.
    """

    private_key: EllipticCurvePrivateKey
    key_id: str

    @property
    def algorithm(self) -> str:
        return WIF_ALGORITHM

    def private_pem(self) -> str:
        """PKCS8 PEM of the PRIVATE key, for the signer only.

        Deliberately a method rather than a stored field so it never lands in a
        dataclass repr, a log record, or a JSON serialisation by accident. The
        only caller is `app/gcp/tokens.py`; nothing on the request path or in any
        response ever touches it.
        """
        return self.private_key.private_bytes(
            encoding=_serialization().Encoding.PEM,
            format=_serialization().PrivateFormat.PKCS8,
            encryption_algorithm=_serialization().NoEncryption(),
        ).decode()

    def public_jwk(self) -> dict[str, Any]:
        """The PUBLIC JWK for this key, in the shape Google's JWKS reader expects.

        RFC 7517. For an EC key that is `kty`, `crv`, `x`, `y` plus the
        descriptive `kid`, `use`, and `alg`. Google's own `--jwk-json-path` help
        text names exactly this field set for an EC key (verified 2026-09-04).

        CONTAINS NO PRIVATE COMPONENT, AND CANNOT. It is built from
        `private_key.public_key()`, so the EC private scalar `d` is not merely
        omitted by this function — it is absent from the object this function
        reads. `tests/unit/test_wif_keys.py` asserts `d` never appears in the
        emitted JWKS, and a serving test asserts the same on the HTTP response.
        """
        from jose import jwk as jose_jwk

        pub_pem = self.private_key.public_key().public_bytes(
            encoding=_serialization().Encoding.PEM,
            format=_serialization().PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        raw = jose_jwk.construct(pub_pem, algorithm=WIF_ALGORITHM).to_dict()
        # python-jose returns bytes for the coordinate fields; a JWKS is JSON.
        out: dict[str, Any] = {
            k: (v.decode() if isinstance(v, bytes) else v) for k, v in raw.items()
        }
        out["kid"] = self.key_id
        out["use"] = "sig"
        out["alg"] = WIF_ALGORITHM
        return out


def _serialization() -> Any:
    from cryptography.hazmat.primitives import serialization

    return serialization


def wif_is_configured(settings: Settings) -> bool:
    """True when the operator has turned the WIF issuer surface ON.

    The issuer base URL is the switch, not the key: a key with nowhere to publish
    its JWKS cannot be federated by anyone, whereas an issuer URL is the value a
    customer pastes into their Google Cloud provider configuration.
    """
    return bool(settings.wif_issuer_base_url.strip())


def load_wif_signing_key(settings: Settings) -> WifSigningKey | None:
    """Return the WIF signing key, or None when the feature is off.

    Resolution order mirrors `app/governance/keys.py::load_signing_key` and
    `bootstrap.py::resolve_credential_encryption_key`, which answer the identical
    question for the other two platform keys:
      1. feature off (no issuer base URL) and no key -> None, feature disabled.
      2. `wif_signing_key_pem` set -> parse, assert P-256, return.
      3. (feature on, production) error - no key, no start.
      4. (feature on, memory backend only) generate an ephemeral P-256 key.

    HALF-CONFIGURED IS REFUSED, both ways. An issuer URL with no key would serve a
    discovery document pointing at a JWKS that cannot be built; a key with no
    issuer URL is a secret held for a surface that does not exist. Both are
    silent failures that would only surface when a customer's federation broke,
    so both fail the boot instead — the same interlock
    `resolve_slack_notifier_config` applies to its own pair (bootstrap.py).
    """
    pem = settings.wif_signing_key_pem.replace("\\n", "\n").strip()
    issuer = settings.wif_issuer_base_url.strip()

    if not issuer and not pem:
        return None

    if not issuer:
        raise WifSigningKeyError(
            "SKYLIZE_WIF_SIGNING_KEY_PEM is set but SKYLIZE_WIF_ISSUER_BASE_URL is "
            "empty. A WIF signing key with no published issuer cannot be federated "
            "by any customer, so this is a half-configured feature that would fail "
            "silently. Set the issuer base URL (e.g. https://oidc.example.com) or "
            "unset the key."
        )

    if pem:
        try:
            pair = ECCService.load_private_key_pem(pem.encode(), curve=WIF_CURVE)
        except Exception as exc:  # noqa: BLE001 — any parse failure is fatal
            raise WifSigningKeyError(
                f"SKYLIZE_WIF_SIGNING_KEY_PEM is set but could not be parsed: {exc}"
            ) from exc
        _assert_p256(pair.private_key)
        return WifSigningKey(
            private_key=pair.private_key,
            key_id=settings.wif_signing_key_id.strip() or "wif-es256-v1",
        )

    if settings.backend != "memory":
        raise WifSigningKeyError(
            "SKYLIZE_WIF_ISSUER_BASE_URL is set but no WIF signing key is "
            "configured. Set SKYLIZE_WIF_SIGNING_KEY_PEM (production must NOT use "
            "an ephemeral key: the JWKS is published to customers' Google Cloud "
            "providers, and a per-pod key would make federation fail on every "
            "replica that did not mint the token). Generate one with "
            "`python scripts/gen_wif_signing_key.py` and hold it as a real secret. "
            "Refusing to start."
        )

    log.warning(
        "No WIF signing key configured; generating an EPHEMERAL P-256 key. "
        "This is allowed only for the in-memory/dev backend and must never be "
        "used in production - every restart invalidates every customer federation."
    )
    pair = ECCService.generate_key_pair(WIF_CURVE)
    return WifSigningKey(
        private_key=pair.private_key,
        key_id=settings.wif_signing_key_id.strip() or "wif-es256-v1",
    )


def _assert_p256(private_key: EllipticCurvePrivateKey) -> None:
    """The WIF curve is fixed at P-256; reject anything else.

    A P-384 PEM here is the specific mistake this catches: it is almost certainly
    the GOVERNANCE key, pasted into the wrong variable. Accepting it would both
    cross the trust domains this module exists to separate AND produce ES384
    tokens that Google rejects. Failing the boot is the only safe response.
    """
    key_size = private_key.curve.key_size
    if key_size != _WIF_KEY_SIZE:
        raise WifSigningKeyError(
            f"WIF signing key must be P-256 (got curve key_size={key_size}). "
            "A P-384 key here is most likely the governance signing key in the "
            "wrong variable: the two keys serve different trust domains and must "
            "never be shared, and Google accepts only RS256 or ES256."
        )
