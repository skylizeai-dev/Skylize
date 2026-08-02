"""Unit / integration tests for agent execution routes (memory backend)."""

from __future__ import annotations


import pytest
from fastapi.testclient import TestClient

from skylize.edge.gateway import create_app

_OWNER_HEADERS = {"X-Dev-Org": "org_a", "X-Dev-User": "u1", "X-Dev-Roles": "owner"}
_VIEWER_HEADERS = {"X-Dev-Org": "org_a", "X-Dev-User": "u2", "X-Dev-Roles": "viewer"}

_VALID_INPUT = {
    "brand_name": "Acme",
    "product_description": "A revolutionary widget",
    "target_audience": "startup founders",
    "tone": "energetic",
}


@pytest.fixture()
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c


# ── GET /api/v1/agents ───────────────────────────────────────────────────────

def test_list_agents_returns_200(client: TestClient) -> None:
    resp = client.get("/api/v1/agents", headers=_OWNER_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert "agents" in body
    assert len(body["agents"]) == 22  # +1: cowork_agent (sandbox)


def test_list_agents_includes_hook_generator(client: TestClient) -> None:
    resp = client.get("/api/v1/agents", headers=_OWNER_HEADERS)
    agents = resp.json()["agents"]
    hook = next((a for a in agents if a["agent_id"] == "hook_generator_agent"), None)
    assert hook is not None
    assert "input_schema" in hook
    assert hook["department"] == "creative"


def test_list_agents_hook_schema_has_required_fields(client: TestClient) -> None:
    resp = client.get("/api/v1/agents", headers=_OWNER_HEADERS)
    agents = resp.json()["agents"]
    hook = next(a for a in agents if a["agent_id"] == "hook_generator_agent")
    props = hook["input_schema"]["properties"]
    assert "brand_name" in props
    assert "product_description" in props
    assert "target_audience" in props


def test_list_agents_viewer_can_see(client: TestClient) -> None:
    resp = client.get("/api/v1/agents", headers=_VIEWER_HEADERS)
    assert resp.status_code == 200


def test_list_agents_unauthenticated_denied(client: TestClient) -> None:
    resp = client.get("/api/v1/agents")
    assert resp.status_code in (401, 403)


# ── POST /api/v1/agents/execute ─────────────────────────────────────────────

def test_execute_returns_201_with_deliverable_id(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/agents/execute",
        json={"agent_id": "hook_generator_agent", "input": _VALID_INPUT},
        headers=_OWNER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "deliverable_id" in body
    assert body["status"] == "draft"
    assert body["agent_id"] == "hook_generator_agent"


def test_execute_deliverable_id_is_uuid(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/agents/execute",
        json={"agent_id": "hook_generator_agent", "input": _VALID_INPUT},
        headers=_OWNER_HEADERS,
    )
    assert resp.status_code == 201
    import uuid
    uuid.UUID(resp.json()["deliverable_id"])  # raises if not valid UUID


def test_execute_unknown_agent_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/agents/execute",
        json={"agent_id": "does_not_exist_agent", "input": {}},
        headers=_OWNER_HEADERS,
    )
    assert resp.status_code == 404


def test_execute_invalid_input_returns_422(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/agents/execute",
        json={"agent_id": "hook_generator_agent", "input": {"wrong": "fields"}},
        headers=_OWNER_HEADERS,
    )
    assert resp.status_code == 422


def test_execute_missing_agent_id_returns_422(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/agents/execute",
        json={"input": _VALID_INPUT},
        headers=_OWNER_HEADERS,
    )
    assert resp.status_code == 422


def test_execute_viewer_role_denied(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/agents/execute",
        json={"agent_id": "hook_generator_agent", "input": _VALID_INPUT},
        headers=_VIEWER_HEADERS,
    )
    assert resp.status_code == 403


def test_execute_unauthenticated_denied(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/agents/execute",
        json={"agent_id": "hook_generator_agent", "input": _VALID_INPUT},
    )
    assert resp.status_code in (401, 403)


# ── Org isolation ────────────────────────────────────────────────────────────

def test_execute_org_isolation(client: TestClient) -> None:
    # Two different orgs execute; each gets its own deliverable.
    r_a = client.post(
        "/api/v1/agents/execute",
        json={"agent_id": "hook_generator_agent", "input": _VALID_INPUT},
        headers={"X-Dev-Org": "org_a", "X-Dev-User": "u1", "X-Dev-Roles": "owner"},
    )
    r_b = client.post(
        "/api/v1/agents/execute",
        json={"agent_id": "hook_generator_agent", "input": _VALID_INPUT},
        headers={"X-Dev-Org": "org_b", "X-Dev-User": "u2", "X-Dev-Roles": "owner"},
    )
    assert r_a.status_code == 201
    assert r_b.status_code == 201
    assert r_a.json()["deliverable_id"] != r_b.json()["deliverable_id"]
