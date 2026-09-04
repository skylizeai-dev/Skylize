"""The OIDC issuer documents Skylize publishes for Workload Identity Federation.

Two static JSON documents and the issuer-URL scheme that ties them together.
Design: docs/06_integrations/gcp_wif_killswitch_design.md §2 (GA-3).

SKYLIZE IS NOT AN OpenID PROVIDER IN THE INTERACTIVE SENSE, and the scope of this
module is the proof. There is no browser, no end user, no consent, and no client:
Skylize signs assertions about ITSELF and hands them to Google's Security Token
Service. So there is no authorization endpoint, no token endpoint, no userinfo,
no dynamic client registration, no session management, and no PKCE. The entire
issuer surface is these two documents plus a JWT signer.

WHY `authorization_endpoint` IS ABSENT, WHICH LOOKS LIKE A SPEC VIOLATION
------------------------------------------------------------------------
OpenID Connect Discovery 1.0 §3 marks `authorization_endpoint` and
`token_endpoint` REQUIRED. This document omits both, deliberately, on empirical
grounds rather than by preference:

  * Google's own requirements never enumerate discovery fields. The SaaS-vendor
    guide requires only that the metadata be publicly discoverable and that the
    JWKS be reachable from `jwks_uri`
    (docs.cloud.google.com/iam/docs/use-workload-identity-federation-to-let-
    customers-access-their-cloud-resources, verified 2026-09-04).
  * GitHub Actions - the most heavily exercised OIDC issuer federated into Google
    Cloud - publishes EXACTLY seven fields and omits both endpoints. Fetched live
    2026-09-04 from https://token.actions.githubusercontent.com/.well-known/
    openid-configuration: {claims_supported, id_token_signing_alg_values_supported,
    issuer, jwks_uri, response_types_supported, scopes_supported,
    subject_types_supported}. Google's workload identity pools accept it at scale.

Publishing a URL that 404s would be worse than omitting the field: it advertises
a capability that does not exist. The field set below is GitHub's, which is
verified-working, and no larger.

WHY THE ISSUER CARRIES NO TRAILING SLASH
----------------------------------------
OIDC Discovery §4 : "If the Issuer value contains a path component, any
terminating / MUST be removed before appending /.well-known/openid-configuration."
An issuer with a trailing slash therefore has TWO plausible written forms while
having one canonical discovery URL, and the `iss` claim must match the customer's
configured `--issuer-uri` exactly. Canonicalising to no-trailing-slash removes the
ambiguity entirely. GitHub's live issuer does the same
("https://token.actions.githubusercontent.com", no trailing slash).

NOTE, because the governing design doc says otherwise: design doc §2.3 wrote the
issuer as `https://oidc.<domain>/w/{slug}/` WITH a trailing slash. That is a
drift, resolved here in favour of the no-slash form on the evidence above. The
discovery URL is identical either way; only the `iss` claim's spelling differs,
and one spelling is better than two.

NO DATABASE ACCESS, BY CONSTRUCTION
-----------------------------------
Every field of both documents is derivable from the issuer slug plus platform
configuration. That is not an optimisation, it is a tenancy control with three
consequences, all load-bearing (design doc §2.4):

  1. The public endpoints need no cross-tenant read, so `gcp_wif_connections` is
     NOT added to migration 0002's carve-out list - preserving the precedent
     0021:63-68 set for the table holding customer credentials.
  2. The endpoints cannot be used as a tenant-existence oracle. They answer
     identically for a slug that exists and one that does not, because they never
     look. A 404-on-unknown-slug implementation would leak the customer list.
  3. They cannot be taken down by a database outage - which matters, because if
     Google cannot fetch the JWKS the token exchange fails with `invalid_grant`
     at precisely the moment a customer is relying on the connection.
"""

from __future__ import annotations

import re
import secrets
from typing import Any

from .keys import WIF_ALGORITHM, WifSigningKey

#: Path segment that namespaces every per-tenant issuer, e.g. `/w/<slug>`. Short
#: on purpose: the default Google audience URL is length-sensitive (Google
#: recommends keeping it under 180 characters) and the slug already costs 26.
ISSUER_PATH_PREFIX = "w"

#: Slug alphabet and length. Crockford-ish base32 without padding: 26 characters
#: of [a-z0-9] is ~134 bits, which is not guessable. Lowercase only so the slug
#: cannot collide case-insensitively in a URL path on a case-folding proxy.
_SLUG_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
SLUG_LENGTH = 26

#: Anchored, total match. This is the ONLY thing standing between a
#: user-supplied URL path segment and the issuer string embedded in a signed
#: token, so it is deliberately an allow-list of two character classes rather
#: than a denial of the dangerous ones.
#:
#: `\Z`, NOT `$`. In Python `$` also matches immediately BEFORE a trailing
#: newline, so `^[a-z0-9]{26}$` accepts "aaa...a\n" - 26 valid characters with a
#: newline smuggled onto the end, which would then be concatenated into an issuer
#: URL and into the `iss` claim of a signed token. `\Z` matches only at the true
#: end of the string. Caught by
#: `tests/unit/test_wif_oidc_documents.py::test_slug_validation_is_anchored_against_embedded_newlines`,
#: which exists precisely because the `$` spelling looked correct.
_SLUG_RE = re.compile(r"\A[a-z0-9]{%d}\Z" % SLUG_LENGTH)


def generate_issuer_slug() -> str:
    """A fresh, unguessable issuer slug.

    `secrets.choice`, not `random`: this value is a public identifier whose whole
    security property is that it cannot be enumerated, and the stdlib `random`
    module is seeded predictably.

    NOT A SECRET, and a future reader must not "fix" that by encrypting the
    column. Possession of the issuer URL grants nothing - only the signing key
    mints tokens. The entropy defeats enumeration; it does not authenticate.
    """
    return "".join(secrets.choice(_SLUG_ALPHABET) for _ in range(SLUG_LENGTH))


def is_valid_issuer_slug(slug: str) -> bool:
    """Whether `slug` is a well-formed issuer slug.

    Rejects everything that is not exactly the expected shape, which incidentally
    rejects `.`, `..`, `/`, and every percent-encoding that could reach a path
    joiner. The route uses this to refuse before building a URL, so no
    caller-controlled text is ever concatenated into an issuer string.
    """
    return bool(_SLUG_RE.match(slug))


def issuer_url(base_url: str, slug: str) -> str:
    """The canonical `iss` value for a tenant: `{base}/w/{slug}`, no trailing slash.

    This exact string goes into the token's `iss` claim, into the discovery
    document's `issuer` field, and into the customer's `--issuer-uri`. All three
    must agree byte for byte, so there is exactly one function that spells it.
    """
    return f"{base_url.rstrip('/')}/{ISSUER_PATH_PREFIX}/{slug}"


def jwks_url(base_url: str, slug: str) -> str:
    """The `jwks_uri` for a tenant.

    PER-TENANT EVEN THOUGH THE KEY SET IS PLATFORM-WIDE TODAY, and that is a
    deliberate, free option on the future. The served document is currently
    identical for every tenant. Routing it per-tenant now means a later move to
    per-tenant signing keys (design doc §4.3) is a server-side change with ZERO
    customer reconfiguration - nobody has to re-run a gcloud command or re-upload
    a key set. A shared `/jwks.json` would make that same change a re-onboarding
    of every customer.
    """
    return f"{issuer_url(base_url, slug)}/jwks.json"


def discovery_document(base_url: str, slug: str) -> dict[str, Any]:
    """The OpenID provider metadata for one tenant issuer.

    Field set and ordering follow GitHub Actions' live document (see module
    docstring), which is verified to work with Google's workload identity pools.
    """
    return {
        "issuer": issuer_url(base_url, slug),
        "jwks_uri": jwks_url(base_url, slug),
        "subject_types_supported": ["public"],
        "response_types_supported": ["id_token"],
        "scopes_supported": ["openid"],
        "id_token_signing_alg_values_supported": [WIF_ALGORITHM],
        "claims_supported": [
            "iss",
            "sub",
            "aud",
            "exp",
            "iat",
            "nbf",
            "jti",
            "skylize_org_id",
            "skylize_purpose",
            "skylize_env",
        ],
    }


def jwks_document(key: WifSigningKey) -> dict[str, Any]:
    """The JWKS for the issuer surface: public key material only.

    One key today. The list shape is what a rotation pass will grow to two
    (current + next) without changing this function's contract or the URL, which
    is why it is a list rather than a bare key even with one entry.
    """
    return {"keys": [key.public_jwk()]}
