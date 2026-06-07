"""Gateway Subsystem-1 flow (memory backend): register, RBAC, API-key auth."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from skylize.edge.gateway import create_app


@pytest.fixture()
def client() -> Iterator[TestClient]:
    # The TestClient context manager runs the lifespan → builds the memory container.
    with TestClient(create_app()) as c:
        yield c


def _owner(org: str = "org_t", user: str = "owner1") -> dict[str, str]:
    return {"X-Dev-Org": org, "X-Dev-User": user, "X-Dev-Roles": "owner"}


def test_register_and_get_me(client: TestClient) -> None:
    resp = client.post("/api/v1/tenants", json={"display_name": "Acme"}, headers=_owner())
    assert resp.status_code == 201, resp.text
    assert resp.json()["org_id"] == "org_t"

    me = client.get("/api/v1/tenants/me", headers=_owner())
    assert me.status_code == 200
    assert me.json()["display_name"] == "Acme"


def test_duplicate_register_conflicts(client: TestClient) -> None:
    headers = _owner("org_dup")
    client.post("/api/v1/tenants", json={"display_name": "A"}, headers=headers)
    again = client.post("/api/v1/tenants", json={"display_name": "A"}, headers=headers)
    assert again.status_code == 409


def test_user_role_management(client: TestClient) -> None:
    headers = _owner("org_rbac")
    client.post("/api/v1/tenants", json={"display_name": "R"}, headers=headers)

    put = client.put(
        "/api/v1/tenants/me/users/analyst1", json={"role": "analyst"}, headers=headers
    )
    assert put.status_code == 200, put.text

    users = client.get("/api/v1/tenants/me/users", headers=headers).json()
    roles = {u["user_id"]: u["role"] for u in users}
    assert roles["analyst1"] == "analyst"


def test_viewer_cannot_manage_users(client: TestClient) -> None:
    headers = _owner("org_v")
    client.post("/api/v1/tenants", json={"display_name": "V"}, headers=headers)

    viewer = {"X-Dev-Org": "org_v", "X-Dev-User": "v", "X-Dev-Roles": "viewer"}
    resp = client.get("/api/v1/tenants/me/users", headers=viewer)
    assert resp.status_code == 403


def test_api_key_issue_list_and_authenticate(client: TestClient) -> None:
    headers = _owner("org_key")
    client.post("/api/v1/tenants", json={"display_name": "K"}, headers=headers)

    issued = client.post(
        "/api/v1/api-keys", json={"name": "ci", "scopes": ["admin"]}, headers=headers
    )
    assert issued.status_code == 201, issued.text
    secret = issued.json()["api_key"]
    assert secret.startswith("sky.")

    # List must expose metadata only — never the secret or its hash.
    listed = client.get("/api/v1/api-keys", headers=headers).json()
    assert len(listed) == 1
    assert "api_key" not in listed[0]
    assert "key_hash" not in listed[0]

    # The key alone (no dev headers) authenticates and resolves the right org.
    me = client.get("/api/v1/tenants/me", headers={"X-API-Key": secret})
    assert me.status_code == 200
    assert me.json()["org_id"] == "org_key"


def test_revoked_api_key_denied(client: TestClient) -> None:
    headers = _owner("org_rev")
    client.post("/api/v1/tenants", json={"display_name": "R"}, headers=headers)

    issued = client.post(
        "/api/v1/api-keys", json={"name": "k", "scopes": ["admin"]}, headers=headers
    ).json()
    secret, key_id = issued["api_key"], issued["key_id"]

    revoke = client.delete(f"/api/v1/api-keys/{key_id}", headers=headers)
    assert revoke.status_code == 204

    denied = client.get("/api/v1/tenants/me", headers={"X-API-Key": secret})
    assert denied.status_code == 401
