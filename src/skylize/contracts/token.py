"""
Governance token signing and validation — the cryptographic foundation.

Governance tokens are signed with **ECDSA P-384** via the existing, tested
`ECCService` (security/ecc_service.py). This is the ratified platform signature
scheme — see ADR 0001 (docs/architecture/adr/0001-governance-signature-scheme.md),
which supersedes the earlier "Ed25519" wording in the foundation docs. P-384 is
FIPS 186-4 approved and is the default in `sign_governance_token`.

This module provides ONLY the stateless cryptographic and structural validation:
canonical serialization, sign, verify-signature, and the ordered validation
pipeline (signature -> expiry -> revocation -> scope -> budget -> delegation).

The *stateful* checks — the live revocation set, agent suspension, and
kill-switch state — are injected through the `LiveStateChecker` protocol and are
implemented by the Governance Authority (later sprint). No DB access here; this
file stays driver-free per the import-linter contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ec import (
    EllipticCurvePrivateKey,
    EllipticCurvePublicKey,
)

from ..security.ecc_service import Curve, ECCService
from .base import AuthorityLevel, GovernanceToken, OnBehalfOf, TokenVersion

# All governance tokens use P-384 (long-lived signing key, higher margin).
GOVERNANCE_CURVE: Curve = Curve.P384


class MalformedToken(ValueError):
    """A token's declared version and its claims contradict each other.

    Raised by `canonical_signing_bytes` rather than returned, because there is no
    correct byte string for a self-contradictory token: signing one would mint a
    lie and verifying one would be answering the wrong question.
    `verify_token_signature` converts it to a plain `False`.
    """


# ---------------------------------------------------------------------------
# Canonical serialization
# ---------------------------------------------------------------------------

def canonical_signing_bytes(
    *,
    token_id: UUID,
    agent_id: str,
    authority_level: str,
    department: str,
    delegation_chain: list[str],
    scope: list[str],
    max_token_budget: int,
    max_execution_time_seconds: int,
    issued_at: datetime,
    expires_at: datetime,
    nonce: str,
    token_version: TokenVersion = "1.0",
    on_behalf_of: OnBehalfOf | None = None,
) -> bytes:
    """Deterministic byte serialization of every token field except `signature`.

    Stable key order, no whitespace, UTC ISO-8601 datetimes — so the bytes a
    signer produces are exactly the bytes a verifier reconstructs.

    VERSIONING — READ BEFORE EDITING THE PAYLOAD BELOW.
    The eleven-key dict is the v1.0 payload and is FROZEN. Every token ever
    signed by this platform is signed over exactly those keys, so adding a key
    to it — even one whose value is null — changes the bytes of every token
    already in flight and invalidates all of them at once. Newer versions
    therefore ADD keys in the branch below, and `token_version` itself is
    absent from a v1.0 payload rather than being written as "1.0".

    That the version is unsigned for v1.0 is not a gap: the signature binds the
    INTERPRETATION. A verifier reconstructs the bytes according to the version
    it is told, so flipping the version flips the reconstruction and the
    signature stops matching. Both downgrade ("1.1"->"1.0", dropping the
    principal claim) and promotion ("1.0"->"1.1", inventing one) are caught
    that way, and each is covered by a test.

    tests/contract/test_token_v10_backcompat.py holds a real token signed
    before this parameter existed and re-verifies it on every run.
    """
    payload = {
        "token_id": str(token_id),
        "agent_id": agent_id,
        "authority_level": authority_level,
        "department": department,
        "delegation_chain": delegation_chain,
        "scope": scope,
        "max_token_budget": max_token_budget,
        "max_execution_time_seconds": max_execution_time_seconds,
        "issued_at": _iso(issued_at),
        "expires_at": _iso(expires_at),
        "nonce": nonce,
    }
    if token_version != "1.0":
        # Refuse to serialize a version/claim mismatch AT ALL, in either
        # direction. `model_copy` bypasses pydantic validators, so a caller can
        # hand us a token whose version and claim disagree; signing or verifying
        # such a thing is never correct. Raising here means:
        #   - the signer cannot burn a signature over bytes that claim "1.1"
        #     while binding no principal (the dangerous direction), and
        #   - the verifier turns the mismatch into a plain False, below.
        if on_behalf_of is None:
            raise MalformedToken(
                f"token_version={token_version!r} requires an on_behalf_of claim"
            )
        payload["token_version"] = token_version
        payload["on_behalf_of"] = {
            "principal_id": on_behalf_of.principal_id,
            "authority_fingerprint": on_behalf_of.authority_fingerprint,
            "session_kind": on_behalf_of.session_kind,
        }
    elif on_behalf_of is not None:
        raise MalformedToken(
            "an on_behalf_of claim cannot be carried by a v1.0 token; the v1.0 "
            "payload is frozen and cannot bind it"
        )
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _iso(dt: datetime) -> str:
    """UTC ISO-8601, normalized so serialization is stable across tz inputs."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def token_signing_bytes(token: GovernanceToken) -> bytes:
    """Canonical signable bytes for an already-constructed token.

    The token's OWN `token_version` selects the canonicalization, so a verifier
    reconstructs whatever the token claims to be — and a mismatch between the
    claimed version and the version actually signed breaks the signature.
    """
    return canonical_signing_bytes(
        token_id=token.token_id,
        agent_id=token.agent_id,
        authority_level=token.authority_level,
        department=token.department,
        delegation_chain=token.delegation_chain,
        scope=token.scope,
        max_token_budget=token.max_token_budget,
        max_execution_time_seconds=token.max_execution_time_seconds,
        issued_at=token.issued_at,
        expires_at=token.expires_at,
        nonce=token.nonce,
        token_version=token.token_version,
        on_behalf_of=token.on_behalf_of,
    )


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------

class TokenSigner:
    """Signs governance tokens. Holds the Governance Authority private key.

    In production only the Governance Authority service instantiates this, with
    the key sourced from the secrets manager. Here it is a thin, testable wrapper
    over `ECCService`.
    """

    def __init__(self, private_key: EllipticCurvePrivateKey) -> None:
        self._private_key = private_key

    def sign(
        self,
        *,
        token_id: UUID,
        agent_id: str,
        authority_level: AuthorityLevel,
        department: str,
        delegation_chain: list[str],
        scope: list[str],
        max_token_budget: int,
        max_execution_time_seconds: int,
        issued_at: datetime,
        expires_at: datetime,
        nonce: str,
        token_version: TokenVersion = "1.0",
        on_behalf_of: OnBehalfOf | None = None,
    ) -> GovernanceToken:
        """Produce a fully-signed `GovernanceToken`.

        `token_version` defaults to "1.0" and `on_behalf_of` to None, so every
        pre-existing call site keeps minting byte-identical v1.0 tokens without
        being touched.

        ORDERING IS LOAD-BEARING: the canonical bytes are built BEFORE the private
        key is touched, so a version/claim mismatch raises `MalformedToken` without
        ever producing a signature. Signing first and validating afterwards would
        burn a real signature over a payload we then refuse to wrap in a token.
        """
        body = canonical_signing_bytes(
            token_id=token_id,
            agent_id=agent_id,
            authority_level=authority_level,
            department=department,
            delegation_chain=delegation_chain,
            scope=scope,
            max_token_budget=max_token_budget,
            max_execution_time_seconds=max_execution_time_seconds,
            issued_at=issued_at,
            expires_at=expires_at,
            nonce=nonce,
            token_version=token_version,
            on_behalf_of=on_behalf_of,
        )
        signature = ECCService.sign_governance_token(
            self._private_key, body, curve=GOVERNANCE_CURVE
        )
        return GovernanceToken(
            token_version=token_version,
            token_id=token_id,
            agent_id=agent_id,
            authority_level=authority_level,
            department=department,
            delegation_chain=delegation_chain,
            scope=scope,
            max_token_budget=max_token_budget,
            max_execution_time_seconds=max_execution_time_seconds,
            issued_at=issued_at,
            expires_at=expires_at,
            nonce=nonce,
            signature=signature,
            on_behalf_of=on_behalf_of,
        )


def verify_token_signature(
    token: GovernanceToken, public_key: EllipticCurvePublicKey
) -> bool:
    """True iff the token's signature verifies against the authority public key.

    A self-contradictory token (version and claim disagreeing, which `model_copy`
    can produce because it skips validators) has no canonical form at all, so it
    cannot verify: the refusal is reported as a plain `False`, exactly like any
    other bad signature. Callers get one answer to one question and never have to
    catch anything.
    """
    try:
        body = token_signing_bytes(token)
    except MalformedToken:
        return False
    return ECCService.verify_governance_token(
        public_key,
        body,
        token.signature,
        curve=GOVERNANCE_CURVE,
    )


# ---------------------------------------------------------------------------
# Validation pipeline (stateless + injected live-state)
# ---------------------------------------------------------------------------

class ValidationStage(str, Enum):
    """The canonical validation order (agent_governance.md §4.3)."""

    SIGNATURE = "signature"
    EXPIRY = "expiry"
    REVOCATION = "revocation"
    SCOPE = "scope"
    BUDGET = "budget"
    DELEGATION = "delegation"


@dataclass(frozen=True, slots=True)
class TokenValidationResult:
    is_valid: bool
    failed_stage: ValidationStage | None = None
    reason: str | None = None

    @classmethod
    def ok(cls) -> "TokenValidationResult":
        return cls(is_valid=True)

    @classmethod
    def fail(cls, stage: ValidationStage, reason: str) -> "TokenValidationResult":
        return cls(is_valid=False, failed_stage=stage, reason=reason)


class LiveStateChecker(Protocol):
    """Injected stateful checks owned by the Governance Authority.

    Foundation defines the seam; the DB-backed implementation arrives with the
    Governance Authority service. Returning a non-None reason means "deny".
    """

    def revocation_reason(self, token_id: UUID, agent_id: str) -> str | None:
        """Reason the token/agent is revoked/suspended/killed, or None if live."""
        ...

    # OPTIONAL, duck-typed on purpose: `authority_stale_reason(claim)`.
    #
    # It is deliberately NOT declared here as a required member. Declaring it
    # would break every existing implementation of this protocol — including
    # `AllowAllLiveState` below and the hand-rolled checkers in the test suite —
    # for tokens that have no principal claim and never needed the check.
    # `validate_tool_call` looks it up with getattr and, for a principal-bound
    # token, DENIES when it is absent. So the lenient typing costs nothing:
    # a checker that does not implement it simply cannot authorize a v1.1 token.


class AllowAllLiveState:
    """No-op live-state checker for tests and pre-Authority foundation use.

    Note it deliberately does NOT implement `authority_stale_reason`: it is an
    allow-all stub, and allowing a principal-bound token whose authority nobody
    verified is exactly the thing that must not happen. A v1.1 token presented
    against this checker is refused at the revocation stage.
    """

    def revocation_reason(self, token_id: UUID, agent_id: str) -> str | None:
        return None


def validate_tool_call(
    *,
    token: GovernanceToken,
    public_key: EllipticCurvePublicKey,
    requested_tool_id: str,
    contract_allowed_tool_ids: set[str],
    requested_token_cost: int,
    tokens_used_so_far: int,
    live_state: LiveStateChecker,
    now: datetime | None = None,
) -> TokenValidationResult:
    """Run the full ordered validation for one tool call.

    Order is canonical and must not change:
      signature -> expiry -> revocation -> scope -> budget -> delegation.
    The first failure short-circuits. No valid token => no side effect.
    """
    now = now or datetime.now(timezone.utc)

    # 1. Signature
    if not verify_token_signature(token, public_key):
        return TokenValidationResult.fail(
            ValidationStage.SIGNATURE, "token signature did not verify"
        )

    # 2. Expiry
    expires_at = token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now >= expires_at:
        return TokenValidationResult.fail(
            ValidationStage.EXPIRY, f"token expired at {_iso(expires_at)}"
        )

    # 3. Revocation / live state (injected)
    reason = live_state.revocation_reason(token.token_id, token.agent_id)
    if reason is not None:
        return TokenValidationResult.fail(ValidationStage.REVOCATION, reason)

    # 3b. Authority freshness, for principal-bound (v1.1) tokens only.
    #
    # This is a REVOCATION check, not a new stage. A human whose grant was
    # withdrawn has had the token's authorization basis revoked; that is the same
    # class of event as a revoked token or a suspended agent, and
    # `principal.errors.StaleAuthority` already declares failed_stage="revocation".
    # Putting it here rather than between scope and budget also fails fast for the
    # right reason: a withdrawn authority voids the token for EVERY tool, so
    # reporting "tool X not in scope" first would be a misleading audit record.
    # Reusing the stage keeps `ValidationStage` unchanged, so nothing that
    # switches on it has to grow a case.
    #
    # Autonomous tokens carry no claim and skip this entirely, which is why every
    # existing caller and every duck-typed test checker is unaffected.
    if token.on_behalf_of is not None:
        stale_check = getattr(live_state, "authority_stale_reason", None)
        if stale_check is None:
            # FAIL CLOSED: a checker that cannot evaluate authority freshness must
            # not be taken as asserting the authority is fresh.
            return TokenValidationResult.fail(
                ValidationStage.REVOCATION,
                "token carries an on_behalf_of claim but this live-state checker "
                "cannot verify authority freshness",
            )
        stale = stale_check(token.on_behalf_of)
        if stale is not None:
            return TokenValidationResult.fail(ValidationStage.REVOCATION, stale)

    # 4. Scope: requested tool in token.scope AND token.scope ⊆ contract tools
    if requested_tool_id not in token.scope:
        return TokenValidationResult.fail(
            ValidationStage.SCOPE,
            f"tool {requested_tool_id!r} not in token scope",
        )
    if not set(token.scope).issubset(contract_allowed_tool_ids):
        return TokenValidationResult.fail(
            ValidationStage.SCOPE,
            "token scope is not a subset of the contract's allowed_tools",
        )

    # 5. Budget / time
    if tokens_used_so_far + requested_token_cost > token.max_token_budget:
        return TokenValidationResult.fail(
            ValidationStage.BUDGET,
            f"call would exceed max_token_budget={token.max_token_budget}",
        )

    # 6. Delegation: well-formed, ends at this agent
    if not token.delegation_chain:
        return TokenValidationResult.fail(
            ValidationStage.DELEGATION, "empty delegation_chain"
        )
    if token.delegation_chain[-1] != token.agent_id:
        return TokenValidationResult.fail(
            ValidationStage.DELEGATION,
            "delegation_chain does not terminate at the token's agent_id",
        )

    return TokenValidationResult.ok()
