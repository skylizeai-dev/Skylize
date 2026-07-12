"""Credential vault HTTP routes — memory backend."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from skylize.edge.gateway import create_app

_OWNER = {"X-Dev-Org": "org_a", "X-Dev-User": "u1", "X-Dev-Roles": "owner"}
_ADMIN = {"X-Dev-Org": "org_a", "X-Dev-User": "u2", "X-Dev-Roles": "admin"}
_VIEWER = {"X-Dev-Org": "org_a", "X-Dev-User": "u3", "X-Dev-Roles": "viewer"}


@pytest.fixture()
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c


# ---------------------------------------------------------------------------
# POST /api/v1/credentials
# ---------------------------------------------------------------------------

def test_store_returns_201(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/credentials",
        json={"provider": "hubspot", "value": "tok_abc"},
        headers=_OWNER,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["provider"] == "hubspot"
    assert body["label"] == ""
    assert "cred_id" in body
    # plaintext must never appear in the response
    assert "value" not in body
    assert "encrypted_value" not in body


def test_store_with_label_and_metadata(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/credentials",
        json={"provider": "hubspot", "value": "tok_main", "label": "main",
              "metadata": {"portal_id": "12345"}},
        headers=_OWNER,
    )
    assert resp.status_code == 201
    assert resp.json()["label"] == "main"


def test_store_admin_allowed(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/credentials",
        json={"provider": "slack", "value": "xoxb"},
        headers=_ADMIN,
    )
    assert resp.status_code == 201


def test_store_viewer_forbidden(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/credentials",
        json={"provider": "hubspot", "value": "tok"},
        headers=_VIEWER,
    )
    assert resp.status_code == 403


def test_store_empty_value_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/credentials",
        json={"provider": "hubspot", "value": ""},
        headers=_OWNER,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/credentials
# ---------------------------------------------------------------------------

def test_list_providers_no_values(client: TestClient) -> None:
    client.post(
        "/api/v1/credentials",
        json={"provider": "slack", "value": "xoxb-secret"},
        headers=_OWNER,
    )
    resp = client.get("/api/v1/credentials", headers=_OWNER)
    assert resp.status_code == 200
    items = resp.json()
    assert any(i["provider"] == "slack" for i in items)
    for item in items:
        # credential values must never appear in list response
        assert "value" not in item
        assert "encrypted_value" not in item


def test_list_providers_viewer_forbidden(client: TestClient) -> None:
    resp = client.get("/api/v1/credentials", headers=_VIEWER)
    assert resp.status_code == 403


def test_list_providers_empty_org(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/credentials",
        headers={"X-Dev-Org": "org_empty", "X-Dev-User": "u", "X-Dev-Roles": "owner"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# DELETE /api/v1/credentials/{id}
# ---------------------------------------------------------------------------

def test_delete_returns_204(client: TestClient) -> None:
    post = client.post(
        "/api/v1/credentials",
        json={"provider": "anytype", "value": "secret"},
        headers=_OWNER,
    )
    cred_id = post.json()["cred_id"]
    resp = client.delete(f"/api/v1/credentials/{cred_id}", headers=_OWNER)
    assert resp.status_code == 204


def test_delete_removes_from_list(client: TestClient) -> None:
    post = client.post(
        "/api/v1/credentials",
        json={"provider": "google_ads", "value": "ads-key"},
        headers=_OWNER,
    )
    cred_id = post.json()["cred_id"]
    client.delete(f"/api/v1/credentials/{cred_id}", headers=_OWNER)
    resp = client.get("/api/v1/credentials", headers=_OWNER)
    assert not any(i["provider"] == "google_ads" for i in resp.json())


def test_delete_nonexistent_404(client: TestClient) -> None:
    resp = client.delete(f"/api/v1/credentials/{uuid.uuid4()}", headers=_OWNER)
    assert resp.status_code == 404


def test_delete_viewer_forbidden(client: TestClient) -> None:
    post = client.post(
        "/api/v1/credentials",
        json={"provider": "slack", "value": "tok"},
        headers=_OWNER,
    )
    cred_id = post.json()["cred_id"]
    resp = client.delete(f"/api/v1/credentials/{cred_id}", headers=_VIEWER)
    assert resp.status_code == 403


def test_cross_tenant_delete_404(client: TestClient) -> None:
    post = client.post(
        "/api/v1/credentials",
        json={"provider": "slack", "value": "tok"},
        headers=_OWNER,
    )
    cred_id = post.json()["cred_id"]
    other = {"X-Dev-Org": "org_other", "X-Dev-User": "u9", "X-Dev-Roles": "owner"}
    resp = client.delete(f"/api/v1/credentials/{cred_id}", headers=other)
    assert resp.status_code == 404
