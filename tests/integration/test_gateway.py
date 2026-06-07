"""API Gateway: health, creative workflow, kill-switch RBAC — memory backend."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from skylize.edge.gateway import create_app


@pytest.fixture()
def client() -> TestClient:
    # TestClient context manager runs the lifespan (builds the memory container).
    with TestClient(create_app()) as c:
        yield c


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_creative_workflow_runs(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/workflows/creative",
        json={"product": "running shoes", "audience": "runners", "count": 2},
        headers={"X-Dev-Org": "org_a", "X-Dev-User": "u1", "X-Dev-Roles": "owner"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["event_type"] == "creative.hooks_generated"
    assert len(body["output"]["hooks"]) == 2


def test_kill_switch_requires_owner(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/kill-switch/engage",
        json={"scope_type": "tenant", "scope_id": "org_a", "reason": "x"},
        headers={"X-Dev-Org": "org_a", "X-Dev-Roles": "viewer"},
    )
    assert resp.status_code == 403


def test_kill_switch_engage_then_workflow_denied(client: TestClient) -> None:
    owner = {"X-Dev-Org": "org_b", "X-Dev-User": "owner1", "X-Dev-Roles": "owner"}
    engaged = client.post(
        "/api/v1/kill-switch/engage",
        json={"scope_type": "tenant", "scope_id": "org_b", "reason": "incident"},
        headers=owner,
    )
    assert engaged.status_code == 200

    run = client.post(
        "/api/v1/workflows/creative",
        json={"product": "x", "audience": "y"},
        headers=owner,
    )
    assert run.status_code == 200
    assert run.json()["status"] == "denied"
