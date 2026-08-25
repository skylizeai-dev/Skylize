"""Auth HTTP routes — memory backend. Covers register/login/me plus the
backward-compat guarantee that dev headers keep working unchanged."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import skylize.config as config_module
from skylize.edge.gateway import create_app

_OWNER = {"X-Dev-Org": "org_a", "X-Dev-User": "u1", "X-Dev-Roles": "owner"}


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch):
    # Reset the Settings singleton so SKYLIZE_JWT_SECRET takes effect for this test.
    monkeypatch.setenv("SKYLIZE_JWT_SECRET", "test-secret")
    config_module._settings = None
    yield
    config_module._settings = None


@pytest.fixture()
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c


def test_register_returns_201(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/auth/register",
        json={"org_id": "org_x", "email": "a@example.com", "password": "hunter2pw"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "a@example.com"
    assert body["roles"] == ["owner"]
    assert "password" not in body
    assert "password_hash" not in body


def test_register_duplicate_email_409(client: TestClient) -> None:
    payload = {"org_id": "org_x", "email": "dup@example.com", "password": "hunter2pw"}
    client.post("/api/v1/auth/register", json=payload)
    resp = client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 409


# ── registration creates NEW orgs only ───────────────────────────────────────

def test_register_into_an_occupied_org_is_409_with_a_code(client: TestClient) -> None:
    """An unauthenticated stranger who knows an org_id must not be admitted to
    that tenant. Before the fix this returned 201 with roles == ["viewer"]."""
    client.post(
        "/api/v1/auth/register",
        json={"org_id": "org_taken", "email": "first@example.com", "password": "hunter2pw"},
    )
    resp = client.post(
        "/api/v1/auth/register",
        json={"org_id": "org_taken", "email": "stranger@example.com", "password": "hunter2pw"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "org_not_available"


def test_refusal_message_does_not_confirm_the_org_exists(client: TestClient) -> None:
    """The status code is a weak existence signal on its own; the message must
    not strengthen it into a confirmation."""
    client.post(
        "/api/v1/auth/register",
        json={"org_id": "org_probe", "email": "first@example.com", "password": "hunter2pw"},
    )
    detail = client.post(
        "/api/v1/auth/register",
        json={"org_id": "org_probe", "email": "stranger@example.com", "password": "hunter2pw"},
    ).json()["detail"]

    assert "not available" in detail
    for confirming in ("already exists", "already registered", "is taken", "has users"):
        assert confirming not in detail, detail
    # Nor may it echo anything about the occupying tenant.
    assert "first@example.com" not in detail


def test_no_account_is_created_by_a_refused_registration(client: TestClient) -> None:
    client.post(
        "/api/v1/auth/register",
        json={"org_id": "org_solo", "email": "first@example.com", "password": "hunter2pw"},
    )
    client.post(
        "/api/v1/auth/register",
        json={"org_id": "org_solo", "email": "stranger@example.com", "password": "hunter2pw"},
    )
    # If a row had been written, the stranger could log in.
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "stranger@example.com", "password": "hunter2pw"},
    )
    assert resp.status_code == 401


def test_a_fresh_org_id_still_registers(client: TestClient) -> None:
    client.post(
        "/api/v1/auth/register",
        json={"org_id": "org_one", "email": "a@example.com", "password": "hunter2pw"},
    )
    resp = client.post(
        "/api/v1/auth/register",
        json={"org_id": "org_two", "email": "b@example.com", "password": "hunter2pw"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["roles"] == ["owner"]


def test_login_returns_200_with_tokens(client: TestClient) -> None:
    client.post(
        "/api/v1/auth/register",
        json={"org_id": "org_x", "email": "a@example.com", "password": "hunter2pw"},
    )
    resp = client.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": "hunter2pw"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "bearer"


def test_login_wrong_password_401(client: TestClient) -> None:
    client.post(
        "/api/v1/auth/register",
        json={"org_id": "org_x", "email": "a@example.com", "password": "hunter2pw"},
    )
    resp = client.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": "nope"}
    )
    assert resp.status_code == 401


def test_me_returns_user_info_with_bearer_token(client: TestClient) -> None:
    client.post(
        "/api/v1/auth/register",
        json={"org_id": "org_x", "email": "a@example.com", "password": "hunter2pw"},
    )
    login_resp = client.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": "hunter2pw"}
    )
    token = login_resp.json()["access_token"]

    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "a@example.com"
    assert body["org_id"] == "org_x"


def test_refresh_rotates_tokens(client: TestClient) -> None:
    client.post(
        "/api/v1/auth/register",
        json={"org_id": "org_x", "email": "a@example.com", "password": "hunter2pw"},
    )
    login_resp = client.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": "hunter2pw"}
    )
    refresh_token = login_resp.json()["refresh_token"]

    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    new_tokens = resp.json()
    assert new_tokens["refresh_token"] != refresh_token

    # Reusing the rotated-out token must fail.
    reuse = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert reuse.status_code == 401


def test_dev_headers_still_work_for_existing_routes(client: TestClient) -> None:
    """Backward compat: X-Dev-* headers continue to authenticate protected
    routes even with SKYLIZE_JWT_SECRET configured."""
    resp = client.post(
        "/api/v1/api-keys",
        json={"name": "ci", "scopes": ["admin"]},
        headers=_OWNER,
    )
    assert resp.status_code == 201


def test_missing_bearer_token_401_without_dev_headers(client: TestClient) -> None:
    resp = client.get("/api/v1/api-keys")
    assert resp.status_code == 401
