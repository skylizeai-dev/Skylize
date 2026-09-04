"""The two published OIDC documents, and the issuer-URL scheme.

The conformance target here is NOT the OpenID Discovery spec's REQUIRED list —
this issuer deliberately omits two of those fields. It is what Google's workload
identity pools actually accept, established empirically against GitHub Actions'
live issuer (fetched 2026-09-04), which Google federates at scale. See
`app/gcp/oidc.py`'s module docstring for the full reasoning and the evidence.
"""

from __future__ import annotations

import pytest

from skylize.app.gcp.keys import load_wif_signing_key
from skylize.app.gcp.oidc import (
    SLUG_LENGTH,
    discovery_document,
    generate_issuer_slug,
    is_valid_issuer_slug,
    issuer_url,
    jwks_document,
    jwks_url,
)
from skylize.config import Settings

BASE = "https://oidc.example.com"
SLUG = "abcdefghijklmnopqrstuvwxyz"[:SLUG_LENGTH]

#: The exact field set GitHub Actions publishes and Google accepts. Pinned as a
#: set so an accidental addition (or a well-meaning "spec compliance" fix that
#: adds a 404-ing authorization_endpoint) fails loudly.
GITHUB_VERIFIED_FIELDS = {
    "issuer",
    "jwks_uri",
    "subject_types_supported",
    "response_types_supported",
    "scopes_supported",
    "id_token_signing_alg_values_supported",
    "claims_supported",
}


def _key():
    key = load_wif_signing_key(
        Settings(backend="memory", wif_issuer_base_url=BASE)
    )
    assert key is not None
    return key


# ---------------------------------------------------------------------------
# Discovery document
# ---------------------------------------------------------------------------

def test_discovery_field_set_matches_the_verified_working_issuer() -> None:
    assert set(discovery_document(BASE, SLUG)) == GITHUB_VERIFIED_FIELDS


def test_discovery_omits_endpoints_that_do_not_exist() -> None:
    """OIDC Discovery marks these REQUIRED; this issuer has neither.

    Publishing a URL that 404s advertises a capability that does not exist, and
    GitHub's live document — federated by Google at scale — omits both. The
    omission is deliberate and evidenced, so it is pinned rather than left to be
    "fixed" by a future reader reading only the spec.
    """
    doc = discovery_document(BASE, SLUG)
    assert "authorization_endpoint" not in doc
    assert "token_endpoint" not in doc
    assert "userinfo_endpoint" not in doc


def test_discovery_issuer_matches_the_canonical_issuer_url() -> None:
    """The `iss` claim, the discovery `issuer` field, and the customer's
    `--issuer-uri` must agree byte for byte. One function spells all three."""
    doc = discovery_document(BASE, SLUG)
    assert doc["issuer"] == issuer_url(BASE, SLUG)
    assert doc["jwks_uri"] == jwks_url(BASE, SLUG)


def test_issuer_has_no_trailing_slash() -> None:
    """OIDC Discovery strips a terminating '/' before appending the well-known
    path, so a trailing slash gives one issuer two spellings and one discovery
    URL. Canonicalising to no-slash removes the ambiguity; GitHub's live issuer
    does the same."""
    assert not issuer_url(BASE, SLUG).endswith("/")
    assert issuer_url(BASE + "/", SLUG) == issuer_url(BASE, SLUG)


def test_jwks_uri_is_per_tenant_even_though_the_key_set_is_shared() -> None:
    """A free option on per-tenant keys later.

    The document is identical for every tenant today. Routing it per-tenant now
    means adopting per-tenant signing keys is a server-side change with zero
    customer reconfiguration, instead of a re-onboarding of everyone.
    """
    other = "z" * SLUG_LENGTH
    assert jwks_url(BASE, SLUG) != jwks_url(BASE, other)
    assert jwks_url(BASE, SLUG).startswith(issuer_url(BASE, SLUG))


def test_declared_algorithm_is_one_google_accepts() -> None:
    algs = discovery_document(BASE, SLUG)["id_token_signing_alg_values_supported"]
    assert algs == ["ES256"], "Google accepts only RS256 or ES256"


# ---------------------------------------------------------------------------
# Slugs
# ---------------------------------------------------------------------------

def test_generated_slugs_are_well_formed_and_not_repeating() -> None:
    slugs = {generate_issuer_slug() for _ in range(50)}
    assert len(slugs) == 50, "slug generation collided — entropy is wrong"
    assert all(is_valid_issuer_slug(s) for s in slugs)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "short",
        "A" * SLUG_LENGTH,          # uppercase: could case-fold-collide in a path
        "a" * (SLUG_LENGTH - 1),
        "a" * (SLUG_LENGTH + 1),
        "../../etc/passwd",
        "abc/def",
        "a" * (SLUG_LENGTH - 2) + "..",
        "a" * (SLUG_LENGTH - 3) + "%2e",
        "a" * (SLUG_LENGTH - 1) + "\n",
    ],
)
def test_malformed_slugs_are_rejected(bad: str) -> None:
    """The validator is the only thing between a URL path segment and the issuer
    string embedded in a signed token, so it is an anchored allow-list rather
    than a denial of the dangerous shapes."""
    assert is_valid_issuer_slug(bad) is False


def test_slug_validation_is_anchored_against_embedded_newlines() -> None:
    """`$` in a Python regex matches before a trailing newline; `\\n` at the end
    of an otherwise-valid slug must still be rejected."""
    assert is_valid_issuer_slug("a" * SLUG_LENGTH + "\n") is False


# ---------------------------------------------------------------------------
# JWKS
# ---------------------------------------------------------------------------

def test_jwks_contains_exactly_one_public_key_and_no_private_material() -> None:
    doc = jwks_document(_key())
    assert list(doc) == ["keys"]
    assert len(doc["keys"]) == 1
    assert "d" not in doc["keys"][0], "EC private scalar leaked into the JWKS"


def test_jwks_is_json_serialisable_with_no_bytes() -> None:
    """python-jose returns bytes for the EC coordinates; a JWKS is JSON.

    A bytes value here would raise at serialisation time inside the request
    handler, i.e. Google would get a 500 while fetching the key set.
    """
    import json

    payload = json.dumps(jwks_document(_key()))
    assert "P-256" in payload
