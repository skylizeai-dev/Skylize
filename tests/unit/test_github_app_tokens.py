"""Installation-token minting: JWT shape, provider-enforced narrowing, and the
failure classification that decides whether a customer gets told to reinstall.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt as jose_jwt

from skylize.app.github.tokens import (
    FORBIDDEN_PERMISSIONS,
    GITHUB_APP_JWT_ALGORITHM,
    JWT_LIFETIME,
    TIER1_PERMISSIONS,
    GithubAppTokenMinter,
    GithubInstallationGone,
    GithubTokenError,
    GithubTransientError,
    InstallationToken,
    assert_permissions_allowed,
    mint_app_jwt,
)

APP_ID = "654321"
INSTALL_ID = 987654


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _public_pem(key: rsa.RSAPrivateKey) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _minter(handler: Any, key: rsa.RSAPrivateKey) -> GithubAppTokenMinter:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GithubAppTokenMinter(app_id=APP_ID, private_key=key, client=client)


def _ok_body(**over: Any) -> dict[str, Any]:
    body = {
        "token": "ghs_exampleinstallationtoken",
        "expires_at": "2026-09-05T21:00:00Z",
        "permissions": {"contents": "write", "metadata": "read"},
        "repository_selection": "selected",
    }
    body.update(over)
    return body


# ---------------------------------------------------------------------------
# The App JWT
# ---------------------------------------------------------------------------

def test_jwt_is_rs256_and_verifies_against_the_public_key(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    """GitHub accepts only RS256 (verified 2026-09-05). Not a preference."""
    token = mint_app_jwt(app_id=APP_ID, private_key=rsa_key)
    header = jose_jwt.get_unverified_header(token)
    assert header["alg"] == GITHUB_APP_JWT_ALGORITHM == "RS256"

    claims = jose_jwt.decode(
        token,
        _public_pem(rsa_key).decode(),
        algorithms=["RS256"],
        options={"verify_aud": False},
    )
    assert claims["iss"] == APP_ID


def test_jwt_lifetime_stays_inside_githubs_ten_minute_cap(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    """`exp` - `iat` must be < 600s, with headroom for server clock skew.

    GitHub rejects an assertion whose exp is more than 10 minutes out with a 401
    that looks exactly like a bad key. Sitting at the cap makes that failure a
    function of the host's clock, which is the worst kind of intermittent.
    """
    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
    token = mint_app_jwt(app_id=APP_ID, private_key=rsa_key, now=now)
    claims = jose_jwt.decode(
        token,
        _public_pem(rsa_key).decode(),
        algorithms=["RS256"],
        options={"verify_aud": False, "verify_exp": False},
    )
    span = claims["exp"] - claims["iat"]
    assert span < 600, f"assertion span {span}s is at or past GitHub's 600s cap"
    assert span <= 9 * 60


def test_iat_is_backdated_for_clock_drift(rsa_key: rsa.RSAPrivateKey) -> None:
    """GitHub itself recommends setting iat 60s in the past."""
    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
    token = mint_app_jwt(app_id=APP_ID, private_key=rsa_key, now=now)
    claims = jose_jwt.decode(
        token,
        _public_pem(rsa_key).decode(),
        algorithms=["RS256"],
        options={"verify_aud": False, "verify_exp": False},
    )
    assert claims["iat"] == int(now.timestamp()) - 60
    assert claims["exp"] == int((now + JWT_LIFETIME).timestamp())


def test_jwt_carries_no_tenant_identity(rsa_key: rsa.RSAPrivateKey) -> None:
    """This credential authenticates the APP, not any customer.

    If a tenant claim ever appears here it would imply per-tenant scoping that
    does not exist at this layer — the narrowing happens on the exchange, not on
    the assertion — and would mislead a future reader into trusting it.
    """
    token = mint_app_jwt(app_id=APP_ID, private_key=rsa_key)
    claims = jose_jwt.get_unverified_claims(token)
    assert set(claims) == {"iss", "iat", "exp"}


# ---------------------------------------------------------------------------
# Attenuation-only: narrowing is mandatory
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unnarrowed_mint_is_refused(rsa_key: rsa.RSAPrivateKey) -> None:
    """Omitting both narrowing args would make GitHub issue an ALL-repository,
    ALL-permission token (verified 2026-09-05).

    That is the widest credential the installation can produce, and it must not be
    reachable by forgetting an argument — it would invert the attenuation-only
    invariant the GitHub App was chosen to enforce.
    """
    called = False

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(201, json=_ok_body())

    with pytest.raises(GithubTokenError, match="exactly one of"):
        await _minter(handler, rsa_key).mint_installation_token(
            installation_id=INSTALL_ID
        )
    assert not called, "refusal must happen before any HTTP call"


@pytest.mark.asyncio
async def test_both_narrowing_args_is_also_refused(rsa_key: rsa.RSAPrivateKey) -> None:
    with pytest.raises(GithubTokenError, match="exactly one of"):
        await _minter(lambda r: httpx.Response(201, json=_ok_body()), rsa_key).mint_installation_token(
            installation_id=INSTALL_ID, repositories=["a"], repository_ids=[1]
        )


@pytest.mark.asyncio
async def test_empty_narrowing_list_is_refused(rsa_key: rsa.RSAPrivateKey) -> None:
    """An empty list is not 'no restriction' and must not be treated as one."""
    with pytest.raises(GithubTokenError, match="empty"):
        await _minter(lambda r: httpx.Response(201, json=_ok_body()), rsa_key).mint_installation_token(
            installation_id=INSTALL_ID, repositories=[]
        )


@pytest.mark.asyncio
async def test_narrowing_and_tier1_permissions_are_sent_on_the_wire(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    """The narrowing must actually reach GitHub, not just be validated locally."""
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json as _json

        seen.update(_json.loads(req.content))
        seen["_url"] = str(req.url)
        seen["_auth"] = req.headers.get("Authorization", "")
        return httpx.Response(201, json=_ok_body())

    await _minter(handler, rsa_key).mint_installation_token(
        installation_id=INSTALL_ID, repositories=["one-repo"]
    )
    assert seen["repositories"] == ["one-repo"]
    assert seen["permissions"] == dict(TIER1_PERMISSIONS)
    assert seen["_url"].endswith(f"/app/installations/{INSTALL_ID}/access_tokens")
    assert seen["_auth"].startswith("Bearer ")


def test_tier1_permission_set_is_exactly_contents_write_and_metadata_read() -> None:
    assert dict(TIER1_PERMISSIONS) == {"contents": "write", "metadata": "read"}


@pytest.mark.parametrize("perm", sorted(FORBIDDEN_PERMISSIONS))
def test_forbidden_permissions_are_refused(perm: str) -> None:
    """Tier 1 is only structural if every path that could widen it refuses."""
    with pytest.raises(GithubTokenError, match="Tier 1"):
        assert_permissions_allowed({perm: "write"})


def test_administration_and_secrets_are_both_forbidden() -> None:
    assert "administration" in FORBIDDEN_PERMISSIONS
    assert "secrets" in FORBIDDEN_PERMISSIONS


@pytest.mark.asyncio
async def test_a_caller_requesting_administration_is_refused_before_http(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    called = False

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(201, json=_ok_body())

    with pytest.raises(GithubTokenError, match="Tier 1"):
        await _minter(handler, rsa_key).mint_installation_token(
            installation_id=INSTALL_ID,
            repositories=["r"],
            permissions={"administration": "write"},
        )
    assert not called


# ---------------------------------------------------------------------------
# Failure classification — which errors are terminal for the TENANT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("code", [404, 410])
async def test_404_means_uninstalled_and_is_terminal(
    rsa_key: rsa.RSAPrivateKey, code: int
) -> None:
    with pytest.raises(GithubInstallationGone):
        await _minter(lambda r: httpx.Response(code, json={}), rsa_key).mint_installation_token(
            installation_id=INSTALL_ID, repositories=["r"]
        )


@pytest.mark.asyncio
async def test_401_is_a_platform_problem_and_never_marks_the_tenant_revoked(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    """A wrong platform key would otherwise mark EVERY customer revoked at once.

    401 means the assertion was rejected — bad app id, wrong/rotated key, or host
    clock skew. None of those is the customer's fault and none of them should
    produce a "please reinstall" message.
    """
    with pytest.raises(GithubTokenError) as ei:
        await _minter(lambda r: httpx.Response(401, json={}), rsa_key).mint_installation_token(
            installation_id=INSTALL_ID, repositories=["r"]
        )
    assert not isinstance(ei.value, GithubInstallationGone)
    assert "PLATFORM problem" in str(ei.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [429, 500, 502, 503])
async def test_transient_codes_raise_the_transient_error(
    rsa_key: rsa.RSAPrivateKey, code: int
) -> None:
    """"We could not check" must never collapse into "there was nothing to find"."""
    with pytest.raises(GithubTransientError):
        await _minter(lambda r: httpx.Response(code, json={}), rsa_key).mint_installation_token(
            installation_id=INSTALL_ID, repositories=["r"]
        )


@pytest.mark.asyncio
async def test_transport_failure_is_transient(rsa_key: rsa.RSAPrivateKey) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns went away")

    with pytest.raises(GithubTransientError):
        await _minter(handler, rsa_key).mint_installation_token(
            installation_id=INSTALL_ID, repositories=["r"]
        )


@pytest.mark.asyncio
async def test_201_without_a_token_is_refused(rsa_key: rsa.RSAPrivateKey) -> None:
    body = _ok_body()
    del body["token"]
    with pytest.raises(GithubTokenError, match="without a token"):
        await _minter(lambda r: httpx.Response(201, json=body), rsa_key).mint_installation_token(
            installation_id=INSTALL_ID, repositories=["r"]
        )


# ---------------------------------------------------------------------------
# The returned value
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_successful_mint_returns_parsed_token(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    tok = await _minter(lambda r: httpx.Response(201, json=_ok_body()), rsa_key).mint_installation_token(
        installation_id=INSTALL_ID, repositories=["r"]
    )
    assert isinstance(tok, InstallationToken)
    assert tok.token == "ghs_exampleinstallationtoken"
    assert tok.expires_at == datetime(2026, 9, 5, 21, 0, tzinfo=timezone.utc)
    assert tok.permissions == {"contents": "write", "metadata": "read"}
    assert tok.repository_selection == "selected"


@pytest.mark.asyncio
async def test_missing_expiry_falls_back_to_one_hour_conservatively(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    """A timestamp format change must not break every governed call.

    The fallback is the DOCUMENTED maximum, so a freshness check against it can
    only be pessimistic, never optimistic.
    """
    body = _ok_body(expires_at="not-a-date")
    before = datetime.now(timezone.utc)
    tok = await _minter(lambda r: httpx.Response(201, json=body), rsa_key).mint_installation_token(
        installation_id=INSTALL_ID, repositories=["r"]
    )
    assert tok.expires_at <= before + timedelta(hours=1, seconds=5)
    assert tok.expires_at > before


@pytest.mark.asyncio
async def test_token_repr_never_leaks_the_bearer(rsa_key: rsa.RSAPrivateKey) -> None:
    tok = await _minter(lambda r: httpx.Response(201, json=_ok_body()), rsa_key).mint_installation_token(
        installation_id=INSTALL_ID, repositories=["r"]
    )
    assert "ghs_" not in repr(tok)
    assert "redacted" in repr(tok)
