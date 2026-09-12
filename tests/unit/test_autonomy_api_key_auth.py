"""API-key auth on the autonomy route — the console proxy's credential path.

Console-Black is a static SPA and can hold no credential of its own, so it
reaches the autonomy route through the website BFF
(``website/src/app/api/console/autonomy/route.ts``), which authenticates with a
service API key in ``X-API-Key`` exactly as every other ``/api/console/*`` route
does (``website/src/lib/skylize/client.ts:120``).

These tests prove that path ALREADY satisfies the route's RBAC with NO change to
``get_context`` or ``require_role``: a key whose scopes include ``owner`` is
accepted on both verbs, and a key without it is still refused on the PUT. That
is the whole reason the proxy design needs no resolver widening, and it is why
the resolver shared with ``kill_switch.py`` stays untouched.

READING THE STATUS CODES. ``_require_dal`` raises 503 on the memory backend
(``edge/routes/autonomy.py:58-64``) and it runs INSIDE the handler — i.e. only
after the dependency chain has already authenticated the caller and checked the
role. So on this backend:

    503 -> the credential resolved AND carried the required role (handler ran)
    401 -> the credential did not resolve at all
    403 -> resolved, but the required role was missing

A 503 is therefore the POSITIVE result here. It is asserted against explicitly
rather than with ``!= 401`` so that a future regression that turned the accept
into a refusal cannot pass.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import skylize.config as config_module
from skylize.edge.gateway import create_app

_OWNER_HDR = {"X-Dev-Org": "org_a", "X-Dev-User": "u1", "X-Dev-Roles": "owner"}

#: The route's DAL is absent on the memory backend, so a handler that RUNS ends
#: here. See the module docstring: this code means auth+RBAC both passed.
_AUTH_PASSED = 503


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SKYLIZE_JWT_SECRET", "test-secret")
    config_module._settings = None
    yield
    config_module._settings = None


@pytest.fixture()
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c


def _issue_key(client: TestClient, scopes: list[str]) -> str:
    """Mint a service key the way an owner would for the proxy, and return the
    plaintext secret (present only in the 201 — api_keys.py:36-37)."""
    r = client.post(
        "/api/v1/api-keys",
        json={"name": f"console-bff-{'-'.join(scopes) or 'none'}", "scopes": scopes},
        headers=_OWNER_HDR,
    )
    assert r.status_code == 201, r.text
    return r.json()["api_key"]


def test_put_accepts_owner_scoped_api_key(client: TestClient) -> None:
    # THE load-bearing assertion: the proxy's own credential clears the
    # owner-only PUT with the resolver exactly as kill_switch.py leaves it.
    key = _issue_key(client, ["owner"])
    r = client.put(
        "/api/v1/autonomy", json={"mode": "propose"}, headers={"X-API-Key": key}
    )
    assert r.status_code == _AUTH_PASSED, r.text


def test_get_accepts_owner_scoped_api_key(client: TestClient) -> None:
    key = _issue_key(client, ["owner"])
    r = client.get("/api/v1/autonomy", headers={"X-API-Key": key})
    assert r.status_code == _AUTH_PASSED, r.text


def test_get_accepts_admin_scoped_api_key(client: TestClient) -> None:
    # The GET is owner-or-admin by design (autonomy.py:10-13, :69).
    key = _issue_key(client, ["admin"])
    r = client.get("/api/v1/autonomy", headers={"X-API-Key": key})
    assert r.status_code == _AUTH_PASSED, r.text


def test_put_refuses_admin_scoped_api_key(client: TestClient) -> None:
    # The PUT stays OWNER ONLY. An admin-scoped key is authenticated but not
    # authorized, so it must stop at 403 and never reach the handler.
    key = _issue_key(client, ["admin"])
    r = client.put(
        "/api/v1/autonomy", json={"mode": "act_governed"}, headers={"X-API-Key": key}
    )
    assert r.status_code == 403, r.text


def test_put_refuses_scopeless_api_key(client: TestClient) -> None:
    # A key issued with no scopes carries no roles at all (service.py:95).
    key = _issue_key(client, [])
    r = client.put(
        "/api/v1/autonomy", json={"mode": "act_governed"}, headers={"X-API-Key": key}
    )
    assert r.status_code == 403, r.text


def test_put_refuses_unknown_api_key(client: TestClient) -> None:
    r = client.put(
        "/api/v1/autonomy",
        json={"mode": "propose"},
        headers={"X-API-Key": "sk_live_notarealkey.notarealsecret"},
    )
    assert r.status_code == 401, r.text


def test_put_rejects_unknown_mode_before_any_write(client: TestClient) -> None:
    # The five named modes are a closed set at the schema boundary, so a bogus
    # mode is a 422 and never reaches the DAL or the audit trail.
    key = _issue_key(client, ["owner"])
    r = client.put(
        "/api/v1/autonomy", json={"mode": "full_send"}, headers={"X-API-Key": key}
    )
    assert r.status_code == 422, r.text
