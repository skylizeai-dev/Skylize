"""Audit read routes: the console's governed-action feed (memory backend)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from skylize.edge.gateway import create_app

_OWNER_A = {"X-Dev-Org": "org_a", "X-Dev-User": "u1", "X-Dev-Roles": "owner"}
_VIEWER_A = {"X-Dev-Org": "org_a", "X-Dev-User": "u2", "X-Dev-Roles": "viewer"}
_OWNER_B = {"X-Dev-Org": "org_b", "X-Dev-User": "u9", "X-Dev-Roles": "owner"}

_VALID_INPUT = {
    "brand_name": "Acme",
    "product_description": "A revolutionary widget",
    "target_audience": "startup founders",
}


@pytest.fixture()
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c


def _run_agent(client: TestClient, headers: dict[str, str]) -> None:
    resp = client.post(
        "/api/v1/agents/execute",
        json={"agent_id": "hook_generator_agent", "input": _VALID_INPUT},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text


def test_audit_feed_records_governed_execution(client: TestClient) -> None:
    _run_agent(client, _OWNER_A)
    resp = client.get("/api/v1/audit", headers=_OWNER_A)
    assert resp.status_code == 200, resp.text
    entries = resp.json()["entries"]
    assert entries, "expected audit entries after a governed run"
    kinds = {e["action_type"] for e in entries}
    assert "agent.executed" in kinds
    top = entries[0]
    assert top["result"] in {"success", "denied", "escalated", "failed"}
    # Single-shot runs are governed: a real minted token id is stamped.
    executed = next(e for e in entries if e["action_type"] == "agent.executed")
    assert executed["governance_token_id"] is not None
    # Hashes only — never clear-text payloads.
    assert "inputs" not in top and "outputs" not in top


def test_audit_feed_is_newest_first_and_paginates(client: TestClient) -> None:
    _run_agent(client, _OWNER_A)
    _run_agent(client, _OWNER_A)
    resp = client.get("/api/v1/audit?limit=1", headers=_OWNER_A)
    body = resp.json()
    assert len(body["entries"]) == 1
    assert body["next_before"] is not None

    older = client.get(
        f"/api/v1/audit?limit=200&before={body['next_before']}", headers=_OWNER_A
    )
    assert older.status_code == 200
    older_entries = older.json()["entries"]
    assert all(e["occurred_at"] < body["next_before"] for e in older_entries)


def test_audit_feed_requires_admin_or_owner(client: TestClient) -> None:
    resp = client.get("/api/v1/audit", headers=_VIEWER_A)
    assert resp.status_code == 403


def test_audit_feed_is_org_scoped(client: TestClient) -> None:
    _run_agent(client, _OWNER_A)
    resp = client.get("/api/v1/audit", headers=_OWNER_B)
    assert resp.status_code == 200
    assert resp.json()["entries"] == []
