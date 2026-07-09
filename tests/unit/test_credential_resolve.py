"""Tests for GET /api/v1/credentials/resolve — org-scoped credential decryption."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from skylize.edge.gateway import create_app

_OWNER = {"X-Dev-Org": "org_a", "X-Dev-User": "u1", "X-Dev-Roles": "owner"}
_ADMIN = {"X-Dev-Org": "org_a", "X-Dev-User": "u2", "X-Dev-Roles": "admin"}
_OPERATOR = {"X-Dev-Org": "org_a", "X-Dev-User": "u3", "X-Dev-Roles": "operator"}
_VIEWER = {"X-Dev-Org": "org_a", "X-Dev-User": "u4", "X-Dev-Roles": "viewer"}
_OTHER_ORG = {"X-Dev-Org": "org_b", "X-Dev-User": "u5", "X-Dev-Roles": "owner"}


@pytest.fixture()
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c


def _store(client: TestClient, provider: str, value: str, label: str = "") -> None:
    body: dict = {"provider": provider, "value": value}
    if label:
        body["label"] = label
    resp = client.post("/api/v1/credentials", json=body, headers=_OWNER)
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_resolve_returns_decrypted_value(client: TestClient) -> None:
    _store(client, "anytype", "secret-key-abc")
    resp = client.get("/api/v1/credentials/resolve", params={"provider": "anytype"}, headers=_OWNER)
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "anytype"
    assert body["value"] == "secret-key-abc"


def test_resolve_admin_allowed(client: TestClient) -> None:
    _store(client, "anytype", "key-for-admin")
    resp = client.get("/api/v1/credentials/resolve", params={"provider": "anytype"}, headers=_ADMIN)
    assert resp.status_code == 200
    assert resp.json()["value"] == "key-for-admin"


def test_resolve_operator_forbidden(client: TestClient) -> None:
    # operator can list provider names but must NOT read decrypted values —
    # /resolve is restricted to owner/admin (matches store/delete).
    _store(client, "anytype", "key-for-operator")
    resp = client.get("/api/v1/credentials/resolve", params={"provider": "anytype"}, headers=_OPERATOR)
    assert resp.status_code == 403


def test_resolve_with_label(client: TestClient) -> None:
    _store(client, "anytype", "labeled-key", label="prod")
    resp = client.get(
        "/api/v1/credentials/resolve",
        params={"provider": "anytype", "label": "prod"},
        headers=_OWNER,
    )
    assert resp.status_code == 200
    assert resp.json()["value"] == "labeled-key"


# ---------------------------------------------------------------------------
# Auth / isolation
# ---------------------------------------------------------------------------

def test_resolve_viewer_forbidden(client: TestClient) -> None:
    _store(client, "anytype", "secret")
    resp = client.get("/api/v1/credentials/resolve", params={"provider": "anytype"}, headers=_VIEWER)
    assert resp.status_code == 403


def test_resolve_cross_tenant_isolation(client: TestClient) -> None:
    """org_b cannot resolve a credential stored by org_a."""
    _store(client, "anytype", "org-a-secret")
    resp = client.get(
        "/api/v1/credentials/resolve",
        params={"provider": "anytype"},
        headers=_OTHER_ORG,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Not found
# ---------------------------------------------------------------------------

def test_resolve_missing_credential_404(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/credentials/resolve",
        params={"provider": "nonexistent"},
        headers=_OWNER,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def test_resolve_rate_limited_after_10_per_minute(client: TestClient) -> None:
    _store(client, "anytype", "ratelimit-key")
    for _ in range(10):
        r = client.get(
            "/api/v1/credentials/resolve",
            params={"provider": "anytype"},
            headers=_OWNER,
        )
        assert r.status_code == 200
    # 11th request must be rate-limited
    r = client.get(
        "/api/v1/credentials/resolve",
        params={"provider": "anytype"},
        headers=_OWNER,
    )
    assert r.status_code == 429


def test_resolve_rate_limit_per_org(client: TestClient) -> None:
    """Rate limit is per org — org_b is not affected by org_a exhausting its budget."""
    _store(client, "anytype", "ratelimit-key")
    # Store same provider for org_b
    client.post(
        "/api/v1/credentials",
        json={"provider": "anytype", "value": "org-b-key"},
        headers=_OTHER_ORG,
    )
    # Exhaust org_a
    for _ in range(10):
        client.get("/api/v1/credentials/resolve", params={"provider": "anytype"}, headers=_OWNER)
    # org_b still has budget
    r = client.get(
        "/api/v1/credentials/resolve",
        params={"provider": "anytype"},
        headers=_OTHER_ORG,
    )
    assert r.status_code == 200
