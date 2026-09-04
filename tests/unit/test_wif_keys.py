"""WIF signing key: custody, fail-closed loading, and the governance separation.

The hard gate this file exists to hold: the WIF issuer key must be at least as
protected as the governance key AND must never share material or an access path
with it. Both halves are asserted here, not assumed.
"""

from __future__ import annotations

import pytest

from skylize.app.gcp.keys import (
    WIF_ALGORITHM,
    WIF_CURVE,
    WifSigningKey,
    WifSigningKeyError,
    load_wif_signing_key,
    wif_is_configured,
)
from skylize.config import Settings
from skylize.security.ecc_service import Curve, ECCService

ISSUER = "https://oidc.example.com"


def _pem(curve: Curve) -> str:
    return ECCService.generate_key_pair(curve).private_pem().decode()


def _settings(**kw) -> Settings:
    base = {"backend": "memory"}
    base.update(kw)
    return Settings(**base)


# ---------------------------------------------------------------------------
# Feature switch
# ---------------------------------------------------------------------------

def test_feature_off_by_default_loads_no_key() -> None:
    """A deployment that has not opted in holds no WIF key at all.

    This is what keeps the pass additive: every existing deployment boots
    unchanged and never generates, holds, or publishes key material.
    """
    settings = _settings()
    assert wif_is_configured(settings) is False
    assert load_wif_signing_key(settings) is None


def test_key_without_issuer_is_refused() -> None:
    """Half-configured, direction one: a key with nowhere to publish its JWKS.

    Nobody could federate against it, so it is a secret held for a surface that
    does not exist — a silent failure, refused at boot instead.
    """
    with pytest.raises(WifSigningKeyError, match="ISSUER_BASE_URL"):
        load_wif_signing_key(_settings(wif_signing_key_pem=_pem(Curve.P256)))


def test_issuer_without_key_fails_closed_on_a_real_backend() -> None:
    """Half-configured, direction two — and the one that must FAIL THE BOOT.

    An issuer URL with no key would serve a discovery document pointing at a JWKS
    that cannot be built. Mirrors `load_signing_key` and
    `resolve_credential_encryption_key`, which both refuse to start rather than
    improvise on a durable backend.
    """
    settings = _settings(
        backend="postgres",
        wif_issuer_base_url=ISSUER,
        db_app_url="postgresql://app:pw@localhost/skylize",
        db_url="postgresql://admin:pw@localhost/skylize",
        dev_auth=False,
        jwt_secret="x" * 32,
        credential_encryption_key="c2t5bGl6ZS1pbnRlZ3JhdGlvbi10ZXN0LWtleSF4MzI=",
        anthropic_api_key="sk-test",
    )
    with pytest.raises(WifSigningKeyError, match="Refusing to start"):
        load_wif_signing_key(settings)


def test_issuer_without_key_mints_an_ephemeral_key_on_memory_only() -> None:
    """The dev escape hatch exists, and only on the memory backend."""
    key = load_wif_signing_key(_settings(wif_issuer_base_url=ISSUER))
    assert isinstance(key, WifSigningKey)
    assert key.private_key.curve.key_size == 256


# ---------------------------------------------------------------------------
# THE SEPARATION FROM THE GOVERNANCE KEY
# ---------------------------------------------------------------------------

def test_the_governance_key_is_rejected_outright() -> None:
    """Pasting the P-384 governance key into the WIF variable must fail the boot.

    This is the specific mistake the curve assertion exists to catch, and it is
    the one that would cross the two trust domains: publishing the governance
    key's public half in a JWKS invites Google's STS to accept anything that key
    signs. It is ALSO technically invalid — Google accepts only RS256/ES256 and
    P-384 signs as ES384 — so accepting it would produce tokens Google rejects.
    """
    with pytest.raises(WifSigningKeyError, match="P-256"):
        load_wif_signing_key(
            _settings(
                wif_issuer_base_url=ISSUER,
                wif_signing_key_pem=_pem(Curve.P384),
            )
        )


def test_governance_and_wif_keys_read_different_settings_fields() -> None:
    """Neither loader can reach the other's material, proven by construction.

    They are given a settings object carrying BOTH keys and must each return
    their own. A shared field, a fallback, or a copy-paste between the two
    loaders would show up here as one loader returning the other's key.
    """
    from skylize.app.governance.keys import load_signing_key

    gov_pem = _pem(Curve.P384)
    wif_pem = _pem(Curve.P256)
    settings = _settings(
        governance_signing_key_pem=gov_pem,
        wif_issuer_base_url=ISSUER,
        wif_signing_key_pem=wif_pem,
    )

    gov = load_signing_key(settings)
    wif = load_wif_signing_key(settings)
    assert wif is not None

    assert gov.private_key.curve.key_size == 384
    assert wif.private_key.curve.key_size == 256
    assert gov.private_key.private_numbers().private_value != (
        wif.private_key.private_numbers().private_value
    )


def test_no_import_edge_between_the_two_key_modules() -> None:
    """The separation is structural, not conventional.

    `app/gcp/keys.py` must not import the governance key module and vice versa,
    so no future refactor can quietly give one module a handle on the other's
    key.

    Asserted over the parsed IMPORT STATEMENTS, not over raw source text: both
    modules discuss the separation in their docstrings, so a substring search
    would match the prose explaining the rule and fail on a compliant file. The
    AST sees only real edges.
    """
    import ast
    import pathlib

    import skylize.app.gcp.keys as wif_keys
    import skylize.app.governance.keys as gov_keys

    def imported_names(module) -> set[str]:
        tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                # Relative imports carry the leading dots in `level`, not in
                # `module`, so record both spellings.
                names.add("." * node.level + (node.module or ""))
        return names

    wif_imports = imported_names(wif_keys)
    gov_imports = imported_names(gov_keys)

    assert not any("governance" in n for n in wif_imports), (
        f"app/gcp/keys.py imports the governance tree: {sorted(wif_imports)}"
    )
    assert not any("gcp" in n for n in gov_imports), (
        f"app/governance/keys.py imports the gcp tree: {sorted(gov_imports)}"
    )


# ---------------------------------------------------------------------------
# Public material only
# ---------------------------------------------------------------------------

def test_public_jwk_carries_no_private_component() -> None:
    """`d` is the EC private scalar. It must never appear in a JWKS.

    Not merely absent from the output: `public_jwk` reads
    `private_key.public_key()`, so `d` is absent from the object the serialiser
    is given. This asserts the observable half of that.
    """
    key = load_wif_signing_key(_settings(wif_issuer_base_url=ISSUER))
    assert key is not None
    jwk = key.public_jwk()
    assert "d" not in jwk
    assert set(jwk) == {"kty", "crv", "x", "y", "kid", "use", "alg"}
    assert jwk["kty"] == "EC"
    assert jwk["crv"] == "P-256"
    assert jwk["alg"] == WIF_ALGORITHM
    assert jwk["use"] == "sig"


def test_private_pem_is_not_exposed_through_repr_or_str() -> None:
    """The private key must not leak through incidental stringification.

    A dataclass that carried the PEM as a FIELD would print it in every repr, and
    reprs reach logs, tracebacks, and error payloads. It is a method instead, so
    this asserts the property that decision buys.
    """
    key = load_wif_signing_key(_settings(wif_issuer_base_url=ISSUER))
    assert key is not None
    rendered = f"{key!r} {key!s}"
    assert "BEGIN PRIVATE KEY" not in rendered
    assert "BEGIN EC PRIVATE KEY" not in rendered
    # The PEM is still reachable deliberately, for the signer only.
    assert "BEGIN PRIVATE KEY" in key.private_pem()


def test_curve_and_algorithm_are_constants_not_configuration() -> None:
    """A deployment must not be able to pick an algorithm Google rejects."""
    assert WIF_CURVE is Curve.P256
    assert WIF_ALGORITHM == "ES256"
