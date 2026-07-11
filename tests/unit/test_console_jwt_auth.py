"""Console (Skylize access JWT) auth on require_role routes.

The web console (website/src/lib/agents.ts, deliverables.ts) sends only
`Authorization: Bearer <skylize access jwt>`. These routes are guarded by
require_any_role_or_user, which accepts that JWT (via get_context_or_user) in
addition to the existing X-Dev-* / OIDC / API-key paths. Regression coverage
confirms the legacy callers still work and bare/bogus callers are still denied.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import skylize.config as config_module
from skylize.edge.gateway import create_app

_OWNER_HDR = {"X-Dev-Org": "org_a", "X-Dev-User": "u1", "X-Dev-Roles": "owner"}


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch):
    # A stable signing key so login-minted tokens verify within the test app.
    monkeypatch.setenv("SKYLIZE_JWT_SECRET", "test-secret")
    config_module._settings = None
    yield
    config_module._settings = None


@pytest.fixture()
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c


def _login_token(client: TestClient) -> str:
    client.post(
        "/api/v1/auth/register",
        json={"org_id": "org_console", "email": "c@example.com", "password": "hunter2pw"},
    )
    r = client.post(
        "/api/v1/auth/login", json={"email": "c@example.com", "password": "hunter2pw"}
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_agents_list_accepts_skylize_jwt(client: TestClient) -> None:
    token = _login_token(client)
    r = client.get("/api/v1/agents", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert "agents" in r.json()


def test_deliverables_list_accepts_skylize_jwt(client: TestClient) -> None:
    token = _login_token(client)
    r = client.get("/api/v1/deliverables", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text


def test_bogus_bearer_still_denied(client: TestClient) -> None:
    # A non-Skylize / invalid Bearer falls through to get_context; with no X-Dev-*
    # header and no API key, dev-auth fails closed (401).
    r = client.get("/api/v1/agents", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert r.status_code == 401


def test_dev_headers_still_work_on_deliverables(client: TestClient) -> None:
    # Regression: existing X-Dev-* callers are unaffected by the JWT-first resolver.
    r = client.get("/api/v1/deliverables", headers=_OWNER_HDR)
    assert r.status_code == 200, r.text
