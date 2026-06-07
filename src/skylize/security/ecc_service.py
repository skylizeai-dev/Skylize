"""
Elliptic Curve Cryptography service for the Skylize platform.

Provides:
  - ECDH key-pair generation (SECP256R1 / P-256, SECP384R1 / P-384)
  - Diffie-Hellman shared-secret derivation + HKDF-based symmetric-key derivation
  - ECDSA signing and verification for agent governance tokens and event payloads
  - Symmetric AEAD encryption/decryption (AES-256-GCM) over ECDH-derived keys
  - Serialisation helpers: PEM, DER, raw-bytes, base64url

All operations use the ``cryptography`` (PyCA) library exclusively — no custom
modular arithmetic.  Keys never leave memory as bare integers.

Curve selection guidance:
  - P-256  (SECP256R1): default; widest library/hardware support, FIPS-140 compliant.
  - P-384  (SECP384R1): higher security margin; preferred for long-lived governance keys.
"""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass
from enum import Enum
from typing import Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import (
    ECDH,
    EllipticCurvePrivateKey,
    EllipticCurvePublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_AES_KEY_BYTES: Final[int] = 32          # AES-256
_GCM_NONCE_BYTES: Final[int] = 12        # 96-bit nonce, GCM standard
_HKDF_HASH = hashes.SHA256()


class Curve(str, Enum):
    """Supported named curves."""
    P256 = "P-256"
    P384 = "P-384"


_CURVE_MAP: dict[Curve, ec.EllipticCurve] = {
    Curve.P256: ec.SECP256R1(),
    Curve.P384: ec.SECP384R1(),
}

_HASH_MAP: dict[Curve, hashes.HashAlgorithm] = {
    Curve.P256: hashes.SHA256(),
    Curve.P384: hashes.SHA384(),
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ECKeyPair:
    """An immutable ECC key pair."""
    private_key: EllipticCurvePrivateKey
    public_key: EllipticCurvePublicKey
    curve: Curve

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def private_pem(self, password: bytes | None = None) -> bytes:
        """Serialize the private key to PKCS8 PEM (optionally AES-256-CBC encrypted)."""
        encryption = (
            serialization.BestAvailableEncryption(password)
            if password
            else serialization.NoEncryption()
        )
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=encryption,
        )

    def public_pem(self) -> bytes:
        """Serialize the public key to SubjectPublicKeyInfo PEM."""
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def public_der(self) -> bytes:
        """Serialize the public key to SubjectPublicKeyInfo DER."""
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def public_b64url(self) -> str:
        """URL-safe base64 of the DER-encoded public key (no padding)."""
        return base64.urlsafe_b64encode(self.public_der()).rstrip(b"=").decode()


@dataclass(frozen=True, slots=True)
class EncryptedPayload:
    """Container for an AES-GCM-encrypted ciphertext produced by ECCService."""
    ciphertext: bytes          # GCM ciphertext + 16-byte tag
    nonce: bytes               # 12-byte GCM nonce
    ephemeral_public_key: bytes  # DER-encoded sender ephemeral public key

    def to_dict(self) -> dict[str, str]:
        """Encode all fields as URL-safe base64 strings for JSON transport."""
        return {
            "ciphertext": _b64u(self.ciphertext),
            "nonce": _b64u(self.nonce),
            "ephemeral_public_key": _b64u(self.ephemeral_public_key),
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> "EncryptedPayload":
        return cls(
            ciphertext=_b64u_decode(data["ciphertext"]),
            nonce=_b64u_decode(data["nonce"]),
            ephemeral_public_key=_b64u_decode(data["ephemeral_public_key"]),
        )


@dataclass(frozen=True, slots=True)
class Signature:
    """A DER-encoded ECDSA signature with provenance metadata."""
    der_bytes: bytes
    curve: Curve
    payload_hash_alg: str      # e.g. "SHA-256"

    def b64url(self) -> str:
        return _b64u(self.der_bytes)

    @classmethod
    def from_b64url(cls, b64url_str: str, curve: Curve, hash_alg: str = "SHA-256") -> "Signature":
        return cls(
            der_bytes=_b64u_decode(b64url_str),
            curve=curve,
            payload_hash_alg=hash_alg,
        )


# ---------------------------------------------------------------------------
# Core service
# ---------------------------------------------------------------------------

class ECCService:
    """
    Stateless ECC utility façade.

    All methods are class methods — instantiation is optional.  The class
    holds no mutable state; secrets live only in caller-managed ``ECKeyPair``
    or ``EllipticCurvePrivateKey`` objects.

    Usage::

        pair = ECCService.generate_key_pair(Curve.P256)
        sig  = ECCService.sign(private_key=pair.private_key, data=b"payload")
        ECCService.verify(public_key=pair.public_key, data=b"payload", signature=sig)

        # ECIES-style encryption
        recipient_pair = ECCService.generate_key_pair()
        enc = ECCService.encrypt(recipient_public_key=recipient_pair.public_key, plaintext=b"secret")
        dec = ECCService.decrypt(recipient_private_key=recipient_pair.private_key, payload=enc)
    """

    # ------------------------------------------------------------------
    # Key generation
    # ------------------------------------------------------------------

    @classmethod
    def generate_key_pair(cls, curve: Curve = Curve.P256) -> ECKeyPair:
        """Generate a fresh ECDH/ECDSA key pair on the requested curve."""
        ec_curve = _CURVE_MAP[curve]
        private_key = ec.generate_private_key(ec_curve)
        return ECKeyPair(
            private_key=private_key,
            public_key=private_key.public_key(),
            curve=curve,
        )

    @classmethod
    def load_private_key_pem(
        cls,
        pem: bytes,
        password: bytes | None = None,
        curve: Curve = Curve.P256,
    ) -> ECKeyPair:
        """Deserialize a PEM private key and return an ECKeyPair."""
        private_key: EllipticCurvePrivateKey = serialization.load_pem_private_key(
            pem, password=password
        )  # type: ignore[assignment]
        return ECKeyPair(
            private_key=private_key,
            public_key=private_key.public_key(),
            curve=curve,
        )

    @classmethod
    def load_public_key_pem(cls, pem: bytes) -> EllipticCurvePublicKey:
        """Deserialize a PEM public key."""
        return serialization.load_pem_public_key(pem)  # type: ignore[return-value]

    @classmethod
    def load_public_key_der(cls, der: bytes) -> EllipticCurvePublicKey:
        """Deserialize a DER public key."""
        return serialization.load_der_public_key(der)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # ECDSA signing & verification
    # ------------------------------------------------------------------

    @classmethod
    def sign(
        cls,
        private_key: EllipticCurvePrivateKey,
        data: bytes,
        curve: Curve = Curve.P256,
    ) -> Signature:
        """
        Sign ``data`` with ``private_key`` using ECDSA + the curve's native
        hash (SHA-256 for P-256, SHA-384 for P-384).

        Returns a DER-encoded signature.
        """
        hash_alg = _HASH_MAP[curve]
        der = private_key.sign(data, ec.ECDSA(hash_alg))
        return Signature(
            der_bytes=der,
            curve=curve,
            payload_hash_alg=hash_alg.name.upper(),
        )

    @classmethod
    def verify(
        cls,
        public_key: EllipticCurvePublicKey,
        data: bytes,
        signature: Signature,
    ) -> None:
        """
        Verify a DER-encoded ECDSA signature.

        Raises ``InvalidSignature`` (from ``cryptography``) on failure.
        This is a strict verify — callers must handle the exception.
        """
        hash_alg = _HASH_MAP[signature.curve]
        public_key.verify(signature.der_bytes, data, ec.ECDSA(hash_alg))

    @classmethod
    def is_valid_signature(
        cls,
        public_key: EllipticCurvePublicKey,
        data: bytes,
        signature: Signature,
    ) -> bool:
        """Boolean-returning wrapper around ``verify``; swallows InvalidSignature."""
        try:
            cls.verify(public_key, data, signature)
            return True
        except InvalidSignature:
            return False

    # ------------------------------------------------------------------
    # ECDH key exchange
    # ------------------------------------------------------------------

    @classmethod
    def derive_shared_secret(
        cls,
        private_key: EllipticCurvePrivateKey,
        peer_public_key: EllipticCurvePublicKey,
    ) -> bytes:
        """
        Compute the raw ECDH shared secret.

        The raw bytes MUST NOT be used as a key directly — always pass through
        ``derive_symmetric_key`` to apply HKDF.
        """
        return private_key.exchange(ECDH(), peer_public_key)

    @classmethod
    def derive_symmetric_key(
        cls,
        shared_secret: bytes,
        salt: bytes | None = None,
        info: bytes = b"skylize-v1",
        key_length: int = _AES_KEY_BYTES,
    ) -> bytes:
        """
        Derive a symmetric key from an ECDH shared secret using HKDF-SHA-256.

        Args:
            shared_secret: Raw bytes from ``derive_shared_secret``.
            salt: Optional random salt (recommended: 32 random bytes).
                  If None, HKDF uses a zero-filled salt of hash length.
            info: Application-specific context string (domain separation).
            key_length: Output key length in bytes (default 32 → AES-256).
        """
        return HKDF(
            algorithm=_HKDF_HASH,
            length=key_length,
            salt=salt,
            info=info,
        ).derive(shared_secret)

    # ------------------------------------------------------------------
    # ECIES-style encrypt / decrypt (ephemeral ECDH + AES-256-GCM)
    # ------------------------------------------------------------------

    @classmethod
    def encrypt(
        cls,
        recipient_public_key: EllipticCurvePublicKey,
        plaintext: bytes,
        aad: bytes | None = None,
        curve: Curve = Curve.P256,
        info: bytes = b"skylize-v1",
    ) -> EncryptedPayload:
        """
        Encrypt ``plaintext`` for ``recipient_public_key`` using ECIES:

        1. Generate an ephemeral key pair.
        2. Compute ECDH(ephemeral_private, recipient_public).
        3. Derive a 256-bit AES key via HKDF.
        4. Encrypt with AES-256-GCM.

        The ephemeral public key is included in the payload so the recipient
        can reproduce step 2.

        Args:
            recipient_public_key: Recipient's long-lived public key.
            plaintext: Bytes to encrypt.
            aad: Optional additional authenticated data (not encrypted, but authenticated).
            curve: Curve to use for the ephemeral key.
            info: HKDF domain-separation string.
        """
        ephemeral = cls.generate_key_pair(curve)
        raw_secret = cls.derive_shared_secret(
            private_key=ephemeral.private_key,
            peer_public_key=recipient_public_key,
        )
        salt = secrets.token_bytes(32)
        symmetric_key = cls.derive_symmetric_key(raw_secret, salt=salt, info=info)

        nonce = secrets.token_bytes(_GCM_NONCE_BYTES)
        aesgcm = AESGCM(symmetric_key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, aad)

        # Pack salt into the nonce field isn't ideal — store it alongside nonce.
        # We embed salt as a prefix in the ciphertext envelope so EncryptedPayload
        # stays a clean three-field struct:  nonce | salt | GCM(ciphertext+tag)
        combined_nonce = nonce + salt   # 12 + 32 = 44 bytes

        return EncryptedPayload(
            ciphertext=ciphertext,
            nonce=combined_nonce,
            ephemeral_public_key=ephemeral.public_der(),
        )

    @classmethod
    def decrypt(
        cls,
        recipient_private_key: EllipticCurvePrivateKey,
        payload: EncryptedPayload,
        aad: bytes | None = None,
        info: bytes = b"skylize-v1",
    ) -> bytes:
        """
        Decrypt an ``EncryptedPayload`` produced by ``encrypt``.

        Raises ``cryptography.exceptions.InvalidTag`` if the ciphertext or AAD
        has been tampered with.
        """
        # Unpack nonce and salt
        nonce = payload.nonce[:_GCM_NONCE_BYTES]
        salt = payload.nonce[_GCM_NONCE_BYTES:]

        ephemeral_public_key = cls.load_public_key_der(payload.ephemeral_public_key)
        raw_secret = cls.derive_shared_secret(
            private_key=recipient_private_key,
            peer_public_key=ephemeral_public_key,
        )
        symmetric_key = cls.derive_symmetric_key(raw_secret, salt=salt, info=info)

        aesgcm = AESGCM(symmetric_key)
        return aesgcm.decrypt(nonce, payload.ciphertext, aad)

    # ------------------------------------------------------------------
    # Governance token helpers (thin wrappers for event-bus auth)
    # ------------------------------------------------------------------

    @classmethod
    def sign_governance_token(
        cls,
        private_key: EllipticCurvePrivateKey,
        token_bytes: bytes,
        curve: Curve = Curve.P384,
    ) -> str:
        """
        Sign a governance token payload and return a URL-safe base64 DER signature.

        Uses P-384 by default for longer-lived governance keys.
        """
        sig = cls.sign(private_key, token_bytes, curve=curve)
        return sig.b64url()

    @classmethod
    def verify_governance_token(
        cls,
        public_key: EllipticCurvePublicKey,
        token_bytes: bytes,
        signature_b64url: str,
        curve: Curve = Curve.P384,
    ) -> bool:
        """
        Verify a signed governance token.  Returns False (never raises) so callers
        get a simple boolean gate without exception-handling boilerplate.
        """
        sig = Signature.from_b64url(signature_b64url, curve=curve)
        return cls.is_valid_signature(public_key, token_bytes, sig)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64u_decode(s: str) -> bytes:
    # Restore padding
    pad = 4 - len(s) % 4
    if pad != 4:
        s += "=" * pad
    return base64.urlsafe_b64decode(s)
