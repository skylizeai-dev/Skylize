"""GitHub App key custody: the platform's third key, and its first RSA one.

These tests pin the properties that make this key SAFE rather than merely working:
that it cannot be confused with either of the other two platform keys, that a
half-configured deployment fails at boot rather than at the first governed call,
and that there is no ephemeral fallback (which for this key would be worthless —
GitHub holds the public half).
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from skylize.app.github.keys import (
    MIN_RSA_MODULUS_BITS,
    GithubAppKey,
    GithubKeyError,
    load_github_app_key,
)
from skylize.config import Settings


def _rsa_pem(bits: int = 2048) -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _ec_pem(curve: ec.EllipticCurve) -> str:
    key = ec.generate_private_key(curve)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _settings(**kw: object) -> Settings:
    base: dict[str, object] = {"backend": "memory"}
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The opt-in contract
# ---------------------------------------------------------------------------

def test_neither_configured_means_feature_off_not_error() -> None:
    """A deployment not using GitHub must behave as if this never shipped.

    Same posture as the Slack notifier: absence of the whole feature is fine.
    """
    assert load_github_app_key(_settings()) is None


def test_key_without_app_id_is_refused_at_boot() -> None:
    """A key with no app id cannot form the JWT `iss` claim.

    Failing here names the missing variable; failing later surfaces as an opaque
    401 from GitHub at the first governed tool call.
    """
    with pytest.raises(GithubKeyError, match="SKYLIZE_GITHUB_APP_ID"):
        load_github_app_key(_settings(github_app_private_key_pem=_rsa_pem()))


def test_app_id_without_key_is_refused_at_boot() -> None:
    with pytest.raises(GithubKeyError, match="PRIVATE_KEY_PEM"):
        load_github_app_key(_settings(github_app_id="123456"))


def test_both_configured_loads() -> None:
    key = load_github_app_key(
        _settings(github_app_id="123456", github_app_private_key_pem=_rsa_pem())
    )
    assert isinstance(key, GithubAppKey)
    assert key.app_id == "123456"
    assert key.private_key.key_size == 2048


# ---------------------------------------------------------------------------
# Key-type separation from the other two platform keys
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "curve,which",
    [(ec.SECP384R1(), "governance P-384"), (ec.SECP256R1(), "WIF P-256")],
)
def test_an_ec_key_is_refused_with_a_message_naming_the_confusion(
    curve: ec.EllipticCurve, which: str
) -> None:
    """The platform's other two keys are EC and neither is a legal GitHub App key.

    GitHub mandates RS256 (verified 2026-09-05). Pasting the governance or WIF key
    into this variable is a realistic mistake — three PEM-shaped secrets in one
    secrets manager — and it must fail loudly at boot rather than 401 later.
    """
    with pytest.raises(GithubKeyError, match="not RSA"):
        load_github_app_key(
            _settings(github_app_id="1", github_app_private_key_pem=_ec_pem(curve))
        )


def test_undersized_rsa_key_is_refused() -> None:
    """GitHub issues 2048-bit keys; anything smaller did not come from GitHub."""
    with pytest.raises(GithubKeyError, match="1024-bit"):
        load_github_app_key(
            _settings(github_app_id="1", github_app_private_key_pem=_rsa_pem(1024))
        )


def test_minimum_modulus_is_2048() -> None:
    assert MIN_RSA_MODULUS_BITS == 2048


def test_unparseable_pem_is_refused_with_actionable_message() -> None:
    with pytest.raises(GithubKeyError, match="could not be parsed"):
        load_github_app_key(
            _settings(github_app_id="1", github_app_private_key_pem="not a pem")
        )


def test_escaped_newlines_are_accepted() -> None:
    """Secret managers and shell exports routinely deliver PEMs with literal \\n.

    The WIF loader handles the same hazard; a key that only works when injected
    one particular way is a deployment trap.
    """
    pem = _rsa_pem().replace("\n", "\\n")
    key = load_github_app_key(
        _settings(github_app_id="7", github_app_private_key_pem=pem)
    )
    assert key is not None
    assert key.app_id == "7"


# ---------------------------------------------------------------------------
# No ephemeral fallback, and no key material in reprs
# ---------------------------------------------------------------------------

def test_no_ephemeral_key_is_minted_on_the_memory_backend() -> None:
    """The other two loaders mint an ephemeral key here. This one must NOT.

    GitHub holds the public half of the real key, registered when the App was
    created. A locally generated pair corresponds to no registered App, so every
    mint would 401. Reporting "feature off" is honest; fabricating a key that
    cannot work is not.
    """
    assert load_github_app_key(_settings(backend="memory")) is None


def test_repr_never_leaks_key_material() -> None:
    """A dataclass' generated repr would print the key object.

    Relying on `cryptography`'s own repr staying opaque is relying on a third
    party's formatting to keep a secret out of logs.
    """
    key = load_github_app_key(
        _settings(github_app_id="42", github_app_private_key_pem=_rsa_pem())
    )
    assert key is not None
    r = repr(key)
    assert "redacted" in r
    assert "42" in r
    assert "PRIVATE KEY" not in r
    assert "BEGIN" not in r


def test_private_pem_roundtrips_for_signing() -> None:
    """The PEM accessor must produce something loadable, since signing needs it."""
    key = load_github_app_key(
        _settings(github_app_id="1", github_app_private_key_pem=_rsa_pem())
    )
    assert key is not None
    reloaded = serialization.load_pem_private_key(key.private_pem(), password=None)
    assert isinstance(reloaded, rsa.RSAPrivateKey)


def test_no_import_edge_to_the_other_key_loaders() -> None:
    """Separation is structural, not conventional.

    `app/gcp/keys.py` states the same property for the WIF key. If a future
    refactor merges these loaders, the three keys' access paths become reachable
    from one another and this test is the tripwire.
    """
    import skylize.app.github.keys as ghkeys

    src = ghkeys.__file__
    assert src is not None
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    # Prose references in the docstring are fine and deliberate; import
    # statements are not.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "governance" not in stripped, line
            assert "gcp" not in stripped, line
            assert "ecc_service" not in stripped, line
