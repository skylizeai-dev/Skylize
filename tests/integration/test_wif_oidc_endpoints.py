"""The public OIDC issuer endpoints, over real HTTP through the gateway.

These are the first deliberately unauthenticated, third-party-consumed routes in
this gateway, so the properties asserted here are the ones that make that safe:
no credential required, no private key material served, no tenant-existence
oracle, and no database access.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from skylize.app.gcp.oidc import SLUG_LENGTH
from skylize.edge.gateway import create_app

ISSUER = "https://oidc.skylize-test.example"
SLUG_A = "a" * SLUG_LENGTH
SLUG_B = "b" * SLUG_LENGTH
DISCOVERY = f"/w/{SLUG_A}/.well-known/openid-configuration"
JWKS = f"/w/{SLUG_A}/jwks.json"


def _reset_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the cached `Settings` singleton so an env change takes effect.

    `get_settings` memoises into a module global (config.py:404-408), so a bare
    `setenv` is invisible to any process that has already built settings. Same
    approach as `test_cors.py::test_wildcard_origin_fails_closed`. Applied to the
    DISABLED fixture too, so it cannot inherit an enabled singleton from a test
    that ran earlier.
    """
    import skylize.config as _cfg

    monkeypatch.setattr(_cfg, "_settings", None)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Gateway with the issuer surface ENABLED, memory backend.

    The key is the ephemeral dev key `load_wif_signing_key` mints on the memory
    backend; nothing here needs a real secret, which is itself the point — no
    test in this repository ever holds production key material.
    """
    monkeypatch.setenv("SKYLIZE_WIF_ISSUER_BASE_URL", ISSUER)
    _reset_settings(monkeypatch)
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def disabled_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Gateway with the issuer surface OFF — the default for every deployment."""
    monkeypatch.delenv("SKYLIZE_WIF_ISSUER_BASE_URL", raising=False)
    _reset_settings(monkeypatch)
    with TestClient(create_app()) as c:
        yield c


# ---------------------------------------------------------------------------
# Unauthenticated by design
# ---------------------------------------------------------------------------

def test_both_endpoints_serve_without_any_credential(client: TestClient) -> None:
    """Google fetches these with no credentials from an IP Skylize does not
    control. A 401/403 here means every customer federation is broken, and the
    breakage surfaces only when a connection is needed."""
    for path in (DISCOVERY, JWKS):
        resp = client.get(path)  # no X-Dev-* headers, no bearer token
        assert resp.status_code == 200, f"{path} required authentication: {resp.text}"


def test_endpoints_ignore_a_garbage_authorization_header(client: TestClient) -> None:
    """A stray credential must not turn a public document into an error."""
    resp = client.get(JWKS, headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Document shape
# ---------------------------------------------------------------------------

def test_discovery_is_google_conformant(client: TestClient) -> None:
    """Conformance target is what Google's pools accept, established against
    GitHub Actions' live issuer (2026-09-04), not the spec's REQUIRED list."""
    resp = client.get(DISCOVERY)
    doc = resp.json()

    assert resp.headers["content-type"].startswith("application/json")
    assert set(doc) == {
        "issuer",
        "jwks_uri",
        "subject_types_supported",
        "response_types_supported",
        "scopes_supported",
        "id_token_signing_alg_values_supported",
        "claims_supported",
    }
    assert doc["issuer"] == f"{ISSUER}/w/{SLUG_A}"
    assert not doc["issuer"].endswith("/")
    assert doc["jwks_uri"] == f"{ISSUER}/w/{SLUG_A}/jwks.json"
    assert doc["id_token_signing_alg_values_supported"] == ["ES256"]


def test_discovery_url_derives_from_the_issuer_exactly_as_the_spec_says(
    client: TestClient,
) -> None:
    """OIDC Discovery: append `/.well-known/openid-configuration` to the issuer
    (after stripping a terminating slash). The document must be reachable at
    precisely the URL a client derives from the `issuer` it advertises."""
    advertised = client.get(DISCOVERY).json()["issuer"]
    derived = advertised.rstrip("/") + "/.well-known/openid-configuration"
    assert derived == ISSUER + DISCOVERY
    assert client.get(derived.removeprefix(ISSUER)).status_code == 200


def test_jwks_serves_public_material_and_is_reachable_from_discovery(
    client: TestClient,
) -> None:
    jwks_uri = client.get(DISCOVERY).json()["jwks_uri"]
    resp = client.get(jwks_uri.removeprefix(ISSUER))
    assert resp.status_code == 200

    doc = resp.json()
    assert list(doc) == ["keys"]
    key = doc["keys"][0]
    assert key["kty"] == "EC"
    assert key["crv"] == "P-256"
    assert key["alg"] == "ES256"
    assert key["use"] == "sig"
    assert key["kid"]


# ---------------------------------------------------------------------------
# THE HARD GATE: no private key material, ever
# ---------------------------------------------------------------------------

def test_no_private_key_material_appears_in_any_response(client: TestClient) -> None:
    """`d` is the EC private scalar; a PEM header would be worse.

    Asserted over the RAW BODY of both documents, not the parsed JSON, so a leak
    anywhere in the payload — a stray field, an error string, a serialised
    exception — is caught rather than only a leak at the expected key.
    """
    for path in (DISCOVERY, JWKS):
        body = client.get(path).text
        assert "BEGIN PRIVATE KEY" not in body
        assert "BEGIN EC PRIVATE KEY" not in body
        parsed = json.loads(body)
        for key in parsed.get("keys", []):
            assert "d" not in key, f"EC private scalar served at {path}"


def test_the_served_jwks_actually_verifies_a_minted_assertion(
    client: TestClient,
) -> None:
    """End to end, exactly as Google does it: fetch the public key set over HTTP,
    then verify an assertion this platform signed. This is the strongest single
    proof that the published material is correct and complete."""
    from jose import jwt as jose_jwt

    from skylize.app.gcp.oidc import issuer_url
    from skylize.app.gcp.tokens import mint_id_token

    served_jwks = client.get(JWKS).json()
    key = client.app.state.container.wif_signing_key  # type: ignore[attr-defined]
    audience = "//iam.googleapis.com/projects/1/locations/global/x/y"

    token = mint_id_token(
        key=key,
        issuer=issuer_url(ISSUER, SLUG_A),
        org_id="org_acme",
        audience=audience,
        environment="test",
    )
    claims = jose_jwt.decode(
        token, served_jwks, algorithms=["ES256"], audience=audience,
        issuer=issuer_url(ISSUER, SLUG_A),
    )
    assert claims["skylize_org_id"] == "org_acme"


# ---------------------------------------------------------------------------
# No tenant-existence oracle
# ---------------------------------------------------------------------------

def test_unknown_slugs_are_indistinguishable_from_known_ones(
    client: TestClient,
) -> None:
    """No connection row exists for EITHER slug in this test, and both answer
    200 with a well-formed document.

    That is the property: the handler never reads the database, so it cannot
    reveal which slugs are owned. A 404-on-unknown-slug implementation would turn
    this endpoint into an enumeration oracle over the customer list.
    """
    a = client.get(f"/w/{SLUG_A}/jwks.json")
    b = client.get(f"/w/{SLUG_B}/jwks.json")
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json()

    da = client.get(f"/w/{SLUG_A}/.well-known/openid-configuration").json()
    db = client.get(f"/w/{SLUG_B}/.well-known/openid-configuration").json()
    assert da["issuer"] != db["issuer"]      # per-tenant issuer
    assert da.keys() == db.keys()            # identical shape either way


@pytest.mark.parametrize(
    "bad_slug",
    ["short", "A" * SLUG_LENGTH, "a" * (SLUG_LENGTH + 1), "with-dash-aaaaaaaaaaaaaa"],
)
def test_malformed_slugs_are_refused(client: TestClient, bad_slug: str) -> None:
    assert client.get(f"/w/{bad_slug}/jwks.json").status_code == 404


# ---------------------------------------------------------------------------
# Off by default
# ---------------------------------------------------------------------------

def test_endpoints_are_absent_when_the_feature_is_not_configured(
    disabled_client: TestClient,
) -> None:
    """Every existing deployment is unchanged by this pass.

    404 rather than 503: to an unauthenticated caller, a deployment that offers
    no issuer surface should be indistinguishable from one where nothing exists.
    """
    assert disabled_client.get(DISCOVERY).status_code == 404
    assert disabled_client.get(JWKS).status_code == 404


def test_caching_headers_are_set_for_a_third_party_fetcher(
    client: TestClient,
) -> None:
    """Both documents are static per deployment. The JWKS max-age also bounds how
    long a future rotation must wait between publishing a key and signing with
    it, so it is set now and measurable."""
    for path in (DISCOVERY, JWKS):
        assert "max-age" in client.get(path).headers.get("cache-control", "")
