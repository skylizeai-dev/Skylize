"""
Comprehensive unit + benchmark tests for ECCService.

Run with:
    pytest tests/unit/test_ecc_service.py -v

Requirements:
    pip install cryptography pytest
"""

from __future__ import annotations

import json
import time
import uuid

import pytest
from cryptography.exceptions import InvalidSignature, InvalidTag

from skylize.security.ecc_service import (
    Curve,
    ECCService,
    ECKeyPair,
    EncryptedPayload,
    Signature,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bench(label: str, fn, iterations: int = 100) -> float:
    """Run ``fn`` ``iterations`` times and return operations-per-second."""
    start = time.perf_counter()
    for _ in range(iterations):
        fn()
    elapsed = time.perf_counter() - start
    ops = iterations / elapsed
    print(f"\n  BENCH [{label}]: {ops:.1f} ops/s  ({elapsed * 1000 / iterations:.2f} ms/op)")
    return ops


# ---------------------------------------------------------------------------
# 1. Key-pair generation
# ---------------------------------------------------------------------------

class TestKeyPairGeneration:
    def test_generates_p256_pair(self):
        pair = ECCService.generate_key_pair(Curve.P256)
        assert isinstance(pair, ECKeyPair)
        assert pair.curve == Curve.P256
        assert pair.private_key is not None
        assert pair.public_key is not None

    def test_generates_p384_pair(self):
        pair = ECCService.generate_key_pair(Curve.P384)
        assert pair.curve == Curve.P384

    def test_default_curve_is_p256(self):
        pair = ECCService.generate_key_pair()
        assert pair.curve == Curve.P256

    def test_each_pair_is_unique(self):
        p1 = ECCService.generate_key_pair()
        p2 = ECCService.generate_key_pair()
        assert p1.public_pem() != p2.public_pem()

    def test_pem_round_trip(self):
        pair = ECCService.generate_key_pair(Curve.P256)
        pem = pair.private_pem()
        loaded = ECCService.load_private_key_pem(pem, curve=Curve.P256)
        assert loaded.public_pem() == pair.public_pem()

    def test_pem_round_trip_encrypted(self):
        password = b"test-p@ss!"
        pair = ECCService.generate_key_pair()
        pem = pair.private_pem(password=password)
        loaded = ECCService.load_private_key_pem(pem, password=password)
        assert loaded.public_pem() == pair.public_pem()

    def test_der_round_trip(self):
        pair = ECCService.generate_key_pair()
        der = pair.public_der()
        loaded = ECCService.load_public_key_der(der)
        assert loaded.public_bytes(
            __import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.DER,
            __import__("cryptography.hazmat.primitives.serialization", fromlist=["PublicFormat"]).PublicFormat.SubjectPublicKeyInfo,
        ) == der

    def test_public_b64url_no_padding(self):
        pair = ECCService.generate_key_pair()
        b64u = pair.public_b64url()
        assert "=" not in b64u
        assert "+" not in b64u
        assert "/" not in b64u

    def test_bench_keygen_p256(self):
        ops = _bench("keygen P-256", lambda: ECCService.generate_key_pair(Curve.P256), iterations=50)
        assert ops > 10, "Key generation should sustain >10 ops/s on any modern CPU"

    def test_bench_keygen_p384(self):
        ops = _bench("keygen P-384", lambda: ECCService.generate_key_pair(Curve.P384), iterations=50)
        assert ops > 5


# ---------------------------------------------------------------------------
# 2. ECDSA signing and verification
# ---------------------------------------------------------------------------

class TestSignAndVerify:
    def test_sign_and_verify_p256(self):
        pair = ECCService.generate_key_pair(Curve.P256)
        data = b"skylize agent payload v1"
        sig = ECCService.sign(pair.private_key, data, curve=Curve.P256)
        ECCService.verify(pair.public_key, data, sig)  # must not raise

    def test_sign_and_verify_p384(self):
        pair = ECCService.generate_key_pair(Curve.P384)
        data = b"governance token content"
        sig = ECCService.sign(pair.private_key, data, curve=Curve.P384)
        ECCService.verify(pair.public_key, data, sig)

    def test_verify_wrong_data_raises(self):
        pair = ECCService.generate_key_pair()
        sig = ECCService.sign(pair.private_key, b"original")
        with pytest.raises(InvalidSignature):
            ECCService.verify(pair.public_key, b"tampered", sig)

    def test_verify_wrong_key_raises(self):
        pair = ECCService.generate_key_pair()
        other = ECCService.generate_key_pair()
        sig = ECCService.sign(pair.private_key, b"data")
        with pytest.raises(InvalidSignature):
            ECCService.verify(other.public_key, b"data", sig)

    def test_is_valid_signature_returns_bool(self):
        pair = ECCService.generate_key_pair()
        data = b"test"
        sig = ECCService.sign(pair.private_key, data)
        assert ECCService.is_valid_signature(pair.public_key, data, sig) is True

    def test_is_valid_signature_false_on_tamper(self):
        pair = ECCService.generate_key_pair()
        sig = ECCService.sign(pair.private_key, b"original")
        assert ECCService.is_valid_signature(pair.public_key, b"tampered", sig) is False

    def test_signature_b64url_round_trip(self):
        pair = ECCService.generate_key_pair()
        data = b"round-trip test"
        sig = ECCService.sign(pair.private_key, data)
        b64u = sig.b64url()
        restored = Signature.from_b64url(b64u, curve=sig.curve, hash_alg=sig.payload_hash_alg)
        assert restored.der_bytes == sig.der_bytes

    def test_empty_payload_signing(self):
        pair = ECCService.generate_key_pair()
        sig = ECCService.sign(pair.private_key, b"")
        ECCService.verify(pair.public_key, b"", sig)

    def test_large_payload_signing(self):
        pair = ECCService.generate_key_pair()
        data = b"x" * 1_000_000  # 1 MB
        sig = ECCService.sign(pair.private_key, data)
        ECCService.verify(pair.public_key, data, sig)

    def test_bench_sign_verify_p256(self):
        pair = ECCService.generate_key_pair()
        data = b"benchmark payload"
        ops_sign = _bench("sign P-256", lambda: ECCService.sign(pair.private_key, data), iterations=200)
        sig = ECCService.sign(pair.private_key, data)
        ops_verify = _bench("verify P-256", lambda: ECCService.verify(pair.public_key, data, sig), iterations=200)
        assert ops_sign > 100, "Sign should sustain >100 ops/s"
        assert ops_verify > 100, "Verify should sustain >100 ops/s"


# ---------------------------------------------------------------------------
# 3. ECDH key exchange and symmetric key derivation
# ---------------------------------------------------------------------------

class TestECDHKeyExchange:
    def test_shared_secrets_match(self):
        alice = ECCService.generate_key_pair()
        bob = ECCService.generate_key_pair()
        s1 = ECCService.derive_shared_secret(alice.private_key, bob.public_key)
        s2 = ECCService.derive_shared_secret(bob.private_key, alice.public_key)
        assert s1 == s2

    def test_different_pairs_produce_different_secrets(self):
        alice = ECCService.generate_key_pair()
        bob = ECCService.generate_key_pair()
        eve = ECCService.generate_key_pair()
        s_ab = ECCService.derive_shared_secret(alice.private_key, bob.public_key)
        s_ae = ECCService.derive_shared_secret(alice.private_key, eve.public_key)
        assert s_ab != s_ae

    def test_derive_symmetric_key_length(self):
        alice = ECCService.generate_key_pair()
        bob = ECCService.generate_key_pair()
        secret = ECCService.derive_shared_secret(alice.private_key, bob.public_key)
        key = ECCService.derive_symmetric_key(secret)
        assert len(key) == 32

    def test_derive_symmetric_key_custom_length(self):
        alice = ECCService.generate_key_pair()
        bob = ECCService.generate_key_pair()
        secret = ECCService.derive_shared_secret(alice.private_key, bob.public_key)
        key = ECCService.derive_symmetric_key(secret, key_length=16)
        assert len(key) == 16

    def test_hkdf_domain_separation(self):
        alice = ECCService.generate_key_pair()
        bob = ECCService.generate_key_pair()
        secret = ECCService.derive_shared_secret(alice.private_key, bob.public_key)
        k1 = ECCService.derive_symmetric_key(secret, info=b"context-a")
        k2 = ECCService.derive_symmetric_key(secret, info=b"context-b")
        assert k1 != k2

    def test_same_salt_produces_same_key(self):
        alice = ECCService.generate_key_pair()
        bob = ECCService.generate_key_pair()
        secret = ECCService.derive_shared_secret(alice.private_key, bob.public_key)
        salt = b"fixed-salt-32bytes-padding-12345"
        k1 = ECCService.derive_symmetric_key(secret, salt=salt)
        k2 = ECCService.derive_symmetric_key(secret, salt=salt)
        assert k1 == k2

    def test_bench_ecdh(self):
        alice = ECCService.generate_key_pair()
        bob = ECCService.generate_key_pair()
        ops = _bench(
            "ECDH derive",
            lambda: ECCService.derive_shared_secret(alice.private_key, bob.public_key),
            iterations=200,
        )
        assert ops > 100


# ---------------------------------------------------------------------------
# 4. ECIES encrypt / decrypt
# ---------------------------------------------------------------------------

class TestEncryptDecrypt:
    def test_basic_encrypt_decrypt(self):
        recipient = ECCService.generate_key_pair()
        plaintext = b"hello skylize"
        enc = ECCService.encrypt(recipient.public_key, plaintext)
        dec = ECCService.decrypt(recipient.private_key, enc)
        assert dec == plaintext

    def test_empty_plaintext(self):
        recipient = ECCService.generate_key_pair()
        enc = ECCService.encrypt(recipient.public_key, b"")
        assert ECCService.decrypt(recipient.private_key, enc) == b""

    def test_large_plaintext(self):
        recipient = ECCService.generate_key_pair()
        plaintext = b"z" * 100_000
        enc = ECCService.encrypt(recipient.public_key, plaintext)
        assert ECCService.decrypt(recipient.private_key, enc) == plaintext

    def test_aad_authenticated(self):
        recipient = ECCService.generate_key_pair()
        plaintext = b"sensitive data"
        aad = b"tenant:org_123"
        enc = ECCService.encrypt(recipient.public_key, plaintext, aad=aad)
        # Correct AAD → succeeds
        assert ECCService.decrypt(recipient.private_key, enc, aad=aad) == plaintext

    def test_tampered_aad_raises(self):
        recipient = ECCService.generate_key_pair()
        enc = ECCService.encrypt(recipient.public_key, b"data", aad=b"original-aad")
        with pytest.raises(InvalidTag):
            ECCService.decrypt(recipient.private_key, enc, aad=b"tampered-aad")

    def test_tampered_ciphertext_raises(self):
        recipient = ECCService.generate_key_pair()
        enc = ECCService.encrypt(recipient.public_key, b"data")
        bad_ct = bytes([enc.ciphertext[0] ^ 0xFF]) + enc.ciphertext[1:]
        bad_enc = EncryptedPayload(
            ciphertext=bad_ct,
            nonce=enc.nonce,
            ephemeral_public_key=enc.ephemeral_public_key,
        )
        with pytest.raises(InvalidTag):
            ECCService.decrypt(recipient.private_key, bad_enc)

    def test_wrong_recipient_raises(self):
        recipient = ECCService.generate_key_pair()
        impostor = ECCService.generate_key_pair()
        enc = ECCService.encrypt(recipient.public_key, b"secret")
        with pytest.raises(InvalidTag):
            ECCService.decrypt(impostor.private_key, enc)

    def test_each_encryption_produces_different_ciphertext(self):
        recipient = ECCService.generate_key_pair()
        plaintext = b"same plaintext"
        enc1 = ECCService.encrypt(recipient.public_key, plaintext)
        enc2 = ECCService.encrypt(recipient.public_key, plaintext)
        assert enc1.ciphertext != enc2.ciphertext  # ephemeral key + nonce differ

    def test_to_dict_from_dict_round_trip(self):
        recipient = ECCService.generate_key_pair()
        plaintext = b"round trip via JSON"
        enc = ECCService.encrypt(recipient.public_key, plaintext)
        wire = json.dumps(enc.to_dict())
        restored = EncryptedPayload.from_dict(json.loads(wire))
        assert ECCService.decrypt(recipient.private_key, restored) == plaintext

    def test_custom_info_domain_separation(self):
        recipient = ECCService.generate_key_pair()
        plaintext = b"domain test"
        enc = ECCService.encrypt(recipient.public_key, plaintext, info=b"skylize-v2")
        # Decrypting with wrong info must fail (different derived key → bad tag)
        with pytest.raises(InvalidTag):
            ECCService.decrypt(recipient.private_key, enc, info=b"skylize-v1")

    def test_bench_encrypt_decrypt(self):
        recipient = ECCService.generate_key_pair()
        plaintext = b"benchmark payload for encryption"
        ops_enc = _bench(
            "ECIES encrypt",
            lambda: ECCService.encrypt(recipient.public_key, plaintext),
            iterations=100,
        )
        enc = ECCService.encrypt(recipient.public_key, plaintext)
        ops_dec = _bench(
            "ECIES decrypt",
            lambda: ECCService.decrypt(recipient.private_key, enc),
            iterations=100,
        )
        assert ops_enc > 50, "Encrypt should sustain >50 ops/s"
        assert ops_dec > 50, "Decrypt should sustain >50 ops/s"


# ---------------------------------------------------------------------------
# 5. Governance token helpers
# ---------------------------------------------------------------------------

class TestGovernanceTokenHelpers:
    def test_sign_and_verify_governance_token(self):
        pair = ECCService.generate_key_pair(Curve.P384)
        token_id = str(uuid.uuid4()).encode()
        token_payload = b'{"authority_level":"director","agent_id":"agent_001","tenant_id":"org_123"}'
        token_bytes = token_id + b"." + token_payload

        sig_b64u = ECCService.sign_governance_token(pair.private_key, token_bytes, curve=Curve.P384)
        assert ECCService.verify_governance_token(pair.public_key, token_bytes, sig_b64u, curve=Curve.P384)

    def test_governance_verify_returns_false_on_tamper(self):
        pair = ECCService.generate_key_pair(Curve.P384)
        token = b"original-token"
        sig_b64u = ECCService.sign_governance_token(pair.private_key, token, curve=Curve.P384)
        assert not ECCService.verify_governance_token(pair.public_key, b"forged-token", sig_b64u, curve=Curve.P384)

    def test_governance_verify_false_on_wrong_key(self):
        pair = ECCService.generate_key_pair(Curve.P384)
        other = ECCService.generate_key_pair(Curve.P384)
        token = b"legit-token"
        sig_b64u = ECCService.sign_governance_token(pair.private_key, token, curve=Curve.P384)
        assert not ECCService.verify_governance_token(other.public_key, token, sig_b64u, curve=Curve.P384)


# ---------------------------------------------------------------------------
# 6. End-to-end: simulated multi-agent workflow
# ---------------------------------------------------------------------------

class TestEndToEndWorkflow:
    """
    Simulates a Skylize agent authentication + secure payload exchange flow:
    - Governance Authority signs a token for Agent A.
    - Agent A encrypts a payload for Agent B using B's public key.
    - Agent B decrypts and verifies the payload.
    """

    def test_full_agent_handshake(self):
        # Governance Authority key (P-384 for long-lived keys)
        gov_pair = ECCService.generate_key_pair(Curve.P384)

        # Agent B has a P-256 key pair (Agent A only signs/encrypts outbound).
        agent_b = ECCService.generate_key_pair(Curve.P256)

        # Step 1: Governance Authority issues a token for Agent A
        token_body = json.dumps({
            "agent_id": "agent_a",
            "authority_level": "worker",
            "tenant_id": "org_test",
            "token_id": str(uuid.uuid4()),
        }).encode()
        gov_sig = ECCService.sign_governance_token(
            gov_pair.private_key, token_body, curve=Curve.P384
        )
        assert ECCService.verify_governance_token(
            gov_pair.public_key, token_body, gov_sig, curve=Curve.P384
        )

        # Step 2: Agent A encrypts a message for Agent B, tags with gov token
        message = json.dumps({
            "event_type": "sales.lead_enriched",
            "payload": {"lead_id": "lead_001"},
            "gov_sig": gov_sig,  # include governance proof in payload
        }).encode()
        aad = b"correlation:corr_001"
        enc_payload = ECCService.encrypt(
            agent_b.public_key, message, aad=aad, curve=Curve.P256
        )

        # Step 3: Agent B decrypts
        decrypted = ECCService.decrypt(agent_b.private_key, enc_payload, aad=aad)
        recovered = json.loads(decrypted)
        assert recovered["event_type"] == "sales.lead_enriched"

        # Step 4: Agent B re-validates the governance signature from the payload
        assert ECCService.verify_governance_token(
            gov_pair.public_key, token_body, recovered["gov_sig"], curve=Curve.P384
        )

    def test_bench_full_workflow(self):
        gov_pair = ECCService.generate_key_pair(Curve.P384)
        agent_b = ECCService.generate_key_pair(Curve.P256)
        token = b"governance-token-body"

        def workflow():
            sig = ECCService.sign_governance_token(gov_pair.private_key, token, curve=Curve.P384)
            enc = ECCService.encrypt(agent_b.public_key, b"event payload", curve=Curve.P256)
            ECCService.decrypt(agent_b.private_key, enc)
            ECCService.verify_governance_token(gov_pair.public_key, token, sig, curve=Curve.P384)

        ops = _bench("full agent workflow", workflow, iterations=50)
        assert ops > 10, "End-to-end workflow should sustain >10 ops/s"
