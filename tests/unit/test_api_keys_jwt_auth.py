"""Console (Skylize access JWT) auth on POST /api/v1/api-keys.

WHY THIS ROUTE MOVED. `POST /api/v1/api-keys` was guarded by `require_any_role`,
whose default resolver is `get_context` -- and `get_context` does not decode a
Skylize access JWT (deps.py:81-94). An owner who registered and logged in
through the console therefore held a token that route refused, so the only ways
to mint a key were an API key (which a fresh deployment does not have) or OIDC.
That is the cycle `skylize.ops.bootstrap_api_key` breaks from outside HTTP; this
change opens the in-band door for an owner who has already authenticated.

THE CHANGE IS A RESOLVER SWAP ON ONE ROUTE, NOT A CHANGE TO `get_context`.
`require_any_role_or_user(*roles)` is `require_any_role(*roles,
resolver=get_context_or_user)` (deps.py:232-236) -- each call builds its own
`_checker` closure over its own resolver, so nothing shared moves. The role set
is still checked, by the same `_checker`, against the same `allowed` frozenset.
`test_get_and_delete_still_refuse_a_jwt` and the kill-switch case below pin that
containment from the outside.

WHAT IS REAL HERE. Registration, login, JWT signing and verification, the role
check, the handler, key persistence and the audit row are all production code
paths. The single stub is the OIDC regression test, which replaces
`build_request_context` -- i.e. exactly the JWKS-verification boundary and
nothing after it -- because verifying a real OIDC token needs a live IdP.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import skylize.config as config_module
from skylize.app.auth.tokens import create_access_token
from skylize.edge.gateway import create_app

_SECRET = "test-secret"
_OWNER_HDR = {"X-Dev-Org": "org_a", "X-Dev-User": "u1", "X-Dev-Roles": "owner"}
_BODY = {"name": "console-minted", "scopes": ["operator"]}


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SKYLIZE_JWT_SECRET", _SECRET)
    config_module._settings = None
    yield
    config_module._settings = None


@pytest.fixture()
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c


def _register_and_login(client: TestClient, org: str = "org_console") -> str:
    """A REAL owner: /auth/register mints a new org and its owner (auth.py:96),
    /auth/login returns that owner's signed access token. No hand-built claims."""
    r = client.post(
        "/api/v1/auth/register",
        json={"org_id": org, "email": f"{org}@example.com", "password": "hunter2pw"},
    )
    assert r.status_code == 201, r.text
    assert "owner" in r.json()["roles"], r.text
    r = client.post(
        "/api/v1/auth/login",
        json={"email": f"{org}@example.com", "password": "hunter2pw"},
    )
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------
# The widening itself
# --------------------------------------------------------------------------

def test_owner_jwt_from_real_login_can_mint_a_key(client: TestClient) -> None:
    # THE load-bearing assertion. Before the resolver swap this was a 401.
    token = _register_and_login(client)
    r = client.post("/api/v1/api-keys", json=_BODY, headers=_bearer(token))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "console-minted"
    assert body["scopes"] == ["operator"]
    # The plaintext secret is present exactly once, in this response.
    assert body["api_key"].count(".") == 2, body["api_key"]


def test_minted_key_is_persisted_and_authenticates(client: TestClient) -> None:
    """201 alone would not prove persistence: assert the row is readable AND
    that the returned secret actually resolves as a credential."""
    token = _register_and_login(client, "org_persist")
    # Owner-scoped so the minted key can itself read the list route (which is
    # still owner-or-admin over get_context) and thereby prove two things at
    # once: the row persisted, and the returned secret authenticates.
    minted = client.post(
        "/api/v1/api-keys", json={"name": "console-minted", "scopes": ["owner"]},
        headers=_bearer(token),
    ).json()

    listed = client.get("/api/v1/api-keys", headers={"X-API-Key": minted["api_key"]})
    assert listed.status_code == 200, listed.text
    assert [k["key_id"] for k in listed.json()] == [minted["key_id"]]
    assert all("api_key" not in k for k in listed.json())  # secret never re-shown
    assert listed.json()[0]["created_by"], "created_by must record the JWT subject"


def test_minting_via_jwt_writes_an_apikey_issued_audit_row(client: TestClient) -> None:
    token = _register_and_login(client, "org_audit")
    minted = client.post("/api/v1/api-keys", json=_BODY, headers=_bearer(token)).json()

    # End-to-end, through the real audit route, as the same owner JWT.
    seen = client.get("/api/v1/audit", headers=_bearer(token))
    assert seen.status_code == 200, seen.text
    issued = [e for e in seen.json()["entries"] if e["action_type"] == "apikey.issued"]
    assert len(issued) == 1, seen.json()["entries"]
    assert issued[0]["result"] == "success"

    # The persisted row keeps only a DIGEST of the inputs (AuditRow has
    # `inputs_hash` and no raw-inputs field at all -- dal/ports.py:53-66), so the
    # plaintext secret cannot reach the trail by construction. Assert both that
    # the digest was recorded and that the secret appears nowhere in the row.
    rows = client.app.state.container.audit._repo.rows  # type: ignore[attr-defined]
    persisted = [r for r in rows if r.action_type == "apikey.issued"]
    assert len(persisted) == 1
    assert persisted[0].org_id == "org_audit"
    assert persisted[0].inputs_hash, "the inputs digest must be recorded"
    assert minted["api_key"] not in str(persisted[0])


# --------------------------------------------------------------------------
# The role check survives the resolver swap (no bypass)
# --------------------------------------------------------------------------

def test_non_owner_jwt_is_refused_with_403(client: TestClient) -> None:
    """A REAL, correctly-signed access token whose roles are outside
    {owner, admin}. It authenticates, so this must be 403 (authorization), not
    401 -- proving the role check still runs on the JWT path."""
    token = create_access_token(
        user_id="u-viewer", org_id="org_console", roles=["viewer"],
        secret=_SECRET, ttl_minutes=5,
    )
    r = client.post("/api/v1/api-keys", json=_BODY, headers=_bearer(token))
    assert r.status_code == 403, r.text
    assert "requires one of roles" in r.text


def test_admin_jwt_is_accepted(client: TestClient) -> None:
    # The route is owner-OR-admin; the swap must not narrow it to owner.
    token = create_access_token(
        user_id="u-admin", org_id="org_console", roles=["admin"],
        secret=_SECRET, ttl_minutes=5,
    )
    r = client.post("/api/v1/api-keys", json=_BODY, headers=_bearer(token))
    assert r.status_code == 201, r.text


def test_unauthenticated_is_refused_with_401(client: TestClient) -> None:
    r = client.post("/api/v1/api-keys", json=_BODY)
    assert r.status_code == 401, r.text


def test_bogus_and_non_access_tokens_are_refused(client: TestClient) -> None:
    # Not a JWT at all -> falls through to get_context -> no creds -> 401.
    assert client.post(
        "/api/v1/api-keys", json=_BODY, headers=_bearer("not-a-real-jwt")
    ).status_code == 401
    # Correctly signed but wrong `type` claim: get_context_or_user must NOT
    # accept it (deps.py:115 checks type == "access").
    from uuid import uuid4

    from skylize.app.auth.tokens import create_refresh_token

    refresh = create_refresh_token(
        user_id="u1", token_id=uuid4(), secret=_SECRET, ttl_days=1
    )
    assert client.post(
        "/api/v1/api-keys", json=_BODY, headers=_bearer(refresh)
    ).status_code == 401


def test_jwt_signed_with_the_wrong_secret_is_refused(client: TestClient) -> None:
    forged = create_access_token(
        user_id="u-forged", org_id="org_console", roles=["owner"],
        secret="not-the-server-secret", ttl_minutes=5,
    )
    assert client.post(
        "/api/v1/api-keys", json=_BODY, headers=_bearer(forged)
    ).status_code == 401


# --------------------------------------------------------------------------
# Regression: the pre-existing auth paths are unchanged (additive, not replaced)
# --------------------------------------------------------------------------

def test_api_key_path_still_mints(client: TestClient) -> None:
    owner_key = client.post(
        "/api/v1/api-keys", json={"name": "seed", "scopes": ["owner"]},
        headers=_OWNER_HDR,
    ).json()["api_key"]
    r = client.post("/api/v1/api-keys", json=_BODY, headers={"X-API-Key": owner_key})
    assert r.status_code == 201, r.text


def test_dev_header_path_still_mints(client: TestClient) -> None:
    r = client.post("/api/v1/api-keys", json=_BODY, headers=_OWNER_HDR)
    assert r.status_code == 201, r.text


def test_scopeless_api_key_still_refused(client: TestClient) -> None:
    weak = client.post(
        "/api/v1/api-keys", json={"name": "weak", "scopes": []}, headers=_OWNER_HDR
    ).json()["api_key"]
    r = client.post("/api/v1/api-keys", json=_BODY, headers={"X-API-Key": weak})
    assert r.status_code == 403, r.text


def test_oidc_path_still_mints(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The OIDC caller still reaches the handler through the new resolver.

    `get_context_or_user` tries the Bearer as a Skylize JWT, fails (this token is
    not signed with our secret), and falls through to `get_context`, which is the
    OIDC path. The stub replaces ONLY `build_request_context` -- the JWKS
    verification step -- so the resolver fall-through, role check, handler and
    audit row under test are all real. `deps.py` binds the name at import, so the
    patch must target `skylize.edge.deps`, not `skylize.edge.auth`.
    """
    from datetime import datetime, timedelta, timezone

    import skylize.edge.deps as deps
    from skylize.schemas.base import RequestContext

    async def _verified_oidc(request, settings):  # noqa: ANN001, ARG001
        return RequestContext(
            org_id="org_oidc", user_id="oidc-subject", roles=["owner"],
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )

    monkeypatch.setattr(deps, "build_request_context", _verified_oidc)
    r = client.post("/api/v1/api-keys", json=_BODY, headers=_bearer("an.oidc.token"))
    assert r.status_code == 201, r.text
    rows = client.app.state.container.audit._repo.rows  # type: ignore[attr-defined]
    assert any(x.org_id == "org_oidc" for x in rows if x.action_type == "apikey.issued")


# --------------------------------------------------------------------------
# Scope containment: nothing else on this router, or elsewhere, moved
# --------------------------------------------------------------------------

def test_get_and_delete_still_refuse_a_jwt(client: TestClient) -> None:
    """Only POST was widened. GET and DELETE still resolve through
    `get_context`, which does not decode a Skylize JWT -> 401."""
    token = _register_and_login(client, "org_scope")
    assert client.get("/api/v1/api-keys", headers=_bearer(token)).status_code == 401
    assert client.delete(
        "/api/v1/api-keys/00000000-0000-0000-0000-000000000000",
        headers=_bearer(token),
    ).status_code == 401


def test_kill_switch_still_refuses_a_jwt(client: TestClient) -> None:
    """The resolver shared with kill_switch.py must not have moved. An owner JWT
    that now mints keys must still be refused here, because the default resolver
    on `require_role` is untouched."""
    token = _register_and_login(client, "org_ks")
    assert client.post(
        "/api/v1/kill-switch/engage", json={"reason": "t"}, headers=_bearer(token)
    ).status_code == 401
    assert client.post(
        "/api/v1/kill-switch/disengage", json={"reason": "t"}, headers=_bearer(token)
    ).status_code == 401


def test_autonomy_put_still_refuses_a_jwt(client: TestClient) -> None:
    # The other owner-only get_context route in the enumeration.
    token = _register_and_login(client, "org_auto")
    assert client.put(
        "/api/v1/autonomy", json={"mode": "propose"}, headers=_bearer(token)
    ).status_code == 401
