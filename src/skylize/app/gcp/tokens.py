"""Minting the OIDC assertion Skylize presents to Google's Security Token Service.

One function that matters: `mint_id_token`. It produces the short-lived, signed
JWT a customer's workload identity pool provider verifies against the JWKS this
platform publishes (`app/gcp/oidc.py`).

WHY `jose.jwt.encode` AND NEVER `ECCService.sign`
-------------------------------------------------
`ECCService.sign` returns a DER-encoded ECDSA signature. JWS (RFC 7515) requires
the raw `R || S` concatenation, which for P-256 is exactly 64 bytes. A token
signed the other way is well-formed JSON with a plausible-looking signature that
Google rejects with an opaque `invalid_grant` - the worst possible failure mode,
because it looks like a customer configuration problem and would be debugged on
the customer's side. `tests/unit/test_wif_tokens.py` pins the 64-byte property.

WHY THE LIFETIME IS 5 MINUTES AND NOT GOOGLE'S 60
-------------------------------------------------
Google caps the federated token it issues at the input token's `exp`, up to one
hour, and recommends keeping ID tokens to 60 minutes
(docs.cloud.google.com/iam/docs/use-workload-identity-federation-to-let-customers-
access-their-cloud-resources, verified 2026-09-04). That is a ceiling, not a
target. Five minutes matches `token_ttl_minutes` (config.py) - the window this
platform already decided is enough for one governed action - and shrinks the
period in which a leaked assertion is useful by an order of magnitude. A single
Compute call plus operation polling fits comfortably inside it.

WHY THE PROBE MINTS THE *SAME* PURPOSE CLAIM THE REAL ACTION WILL USE
---------------------------------------------------------------------
`skylize_purpose` is the claim a customer pins in their provider's attribute
condition, and their IAM binding is granted to the principal that claim selects.
If the health probe minted a different purpose, it would authenticate as a
DIFFERENT principal than the one that will eventually act - and would then prove
nothing about whether the real action can be authorized. The probe must present
the identity it is vouching for. That is why `PURPOSE_COMPUTE_STOP` is shared
between this module's only two callers rather than being a per-call string.

NOTE ON THE NAME. `killswitch` appears in the `sub` and `skylize_purpose` values
because those are the CUSTOMER-FACING wire contract fixed by the design doc
(§5.1): the customer pastes them into their IAM configuration and changing them
later breaks every existing federation. It refers to the customer-facing product
concept, NOT to this platform's internal governance kill switch
(`app/governance/authority.py`), which is a different mechanism that this code
never touches.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from .keys import WIF_ALGORITHM, WifSigningKey

#: The constant `skylize_purpose` claim for compute-stop federation. A customer's
#: attribute condition pins this exact string, so it is a wire contract: changing
#: it silently breaks every customer whose condition names the old value.
PURPOSE_COMPUTE_STOP = "killswitch.compute.stop"

#: Assertion lifetime. See the module docstring - a ceiling of 60 minutes exists
#: and is deliberately not used.
TOKEN_LIFETIME = timedelta(minutes=5)

#: Tolerance for clock skew between Skylize and Google, applied to `nbf` only.
#: `exp` is NOT extended by it: being generous about when a token STOPS being
#: valid is the direction that costs security.
CLOCK_SKEW_LEEWAY = timedelta(seconds=30)

#: Google maps `sub` into `google.subject`, which has a documented 127-byte limit
#: ("The size of mapped attribute google.subject exceeds the 127 bytes limit",
#: docs.cloud.google.com/iam/docs/troubleshooting-workload-identity-federation,
#: verified 2026-09-04). Exceeding it fails the exchange at the moment of use, so
#: it is checked at mint time and, better, at onboarding.
MAX_SUBJECT_BYTES = 127


class WifTokenError(RuntimeError):
    """Raised when an assertion cannot be minted correctly. Always a Skylize-side
    fault (bad configuration, oversized subject) and never evidence that the
    customer's trust relationship is broken - so it must NEVER be classified as a
    revocation."""


def subject_for(org_id: str) -> str:
    """The stable `sub` claim for an org.

    STABLE ON PURPOSE, and this is a constraint rather than a convenience. The
    customer grants an IAM role to `principal://.../subject/<this value>`. A
    subject that varied per call - carrying a decision id, say - would be
    unbindable, because a role cannot be granted to an unbounded set of
    principals. Per-call context travels as a mapped ATTRIBUTE instead.
    """
    return f"org:{org_id}:killswitch"


def assert_subject_fits(org_id: str) -> None:
    """Raise if this org's subject would exceed Google's 127-byte limit.

    Exposed separately so ONBOARDING can call it: the failure is deterministic
    per org, so discovering it when the connection is created costs a validation
    error, while discovering it at mint time costs a broken federation found
    during an incident.
    """
    subject = subject_for(org_id)
    size = len(subject.encode("utf-8"))
    if size > MAX_SUBJECT_BYTES:
        raise WifTokenError(
            f"subject {subject!r} is {size} bytes; Google maps `sub` into "
            f"google.subject which is limited to {MAX_SUBJECT_BYTES} bytes. "
            "Shorten the org_id."
        )


def mint_id_token(
    *,
    key: WifSigningKey,
    issuer: str,
    org_id: str,
    audience: str,
    environment: str,
    purpose: str = PURPOSE_COMPUTE_STOP,
    now: datetime | None = None,
) -> str:
    """Return a signed ES256 assertion for one federation exchange.

    `audience` is passed in rather than derived: the customer may have configured
    a custom allowed audience, and the stored value is the one their provider
    will actually accept (see `GcpWifConnectionRow.audience`).
    """
    from jose import jwt as jose_jwt

    assert_subject_fits(org_id)
    issued = now or datetime.now(timezone.utc)

    claims = {
        "iss": issuer,
        "sub": subject_for(org_id),
        "aud": audience,
        "iat": int(issued.timestamp()),
        "nbf": int((issued - CLOCK_SKEW_LEEWAY).timestamp()),
        "exp": int((issued + TOKEN_LIFETIME).timestamp()),
        "jti": uuid.uuid4().hex,
        # Context claims. Google recommends carrying these so customers can pin
        # them in an attribute condition and see them in their own Cloud Audit
        # Logs. `skylize_org_id` is the tenant pin; `skylize_purpose` scopes what
        # the assertion may be used for; `skylize_env` stops a staging deployment
        # reaching a production project.
        "skylize_org_id": org_id,
        "skylize_purpose": purpose,
        "skylize_env": environment,
    }

    try:
        # `python-jose` is untyped, so `encode` is `Any`. Bound to an annotated
        # local rather than returned directly: mypy's `no-any-return` is doing
        # real work here — it is the check that would catch this function
        # silently starting to return something other than a compact JWS.
        token: str = jose_jwt.encode(
            claims,
            key.private_pem(),
            algorithm=WIF_ALGORITHM,
            headers={"kid": key.key_id},
        )
    except Exception as exc:  # noqa: BLE001 - a mint failure is always ours
        raise WifTokenError(f"could not sign WIF assertion: {exc}") from exc
    return token
