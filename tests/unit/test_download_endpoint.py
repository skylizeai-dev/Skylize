"""Tests for GET /api/v1/deliverables/{id}/download with format dispatch."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from skylize.edge.gateway import create_app

_OWNER = {"X-Dev-Org": "org_a", "X-Dev-User": "u1", "X-Dev-Roles": "owner"}
_OTHER = {"X-Dev-Org": "org_b", "X-Dev-User": "u9", "X-Dev-Roles": "owner"}


@pytest.fixture()
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c  # type: ignore[misc]


@pytest.fixture()
def deliverable_id(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/deliverables",
        json={
            "agent_id": "agent-1",
            "deliverable_type": "blog_post",
            "title": "Q3 Marketing Strategy v2",
            "content_markdown": "# Hello\n\nTest content.\n",
        },
        headers=_OWNER,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Format dispatching
# ---------------------------------------------------------------------------

def test_download_default_is_md(client: TestClient, deliverable_id: str) -> None:
    resp = client.get(f"/api/v1/deliverables/{deliverable_id}/download", headers=_OWNER)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert ".md" in resp.headers["content-disposition"]


def test_download_md_explicit(client: TestClient, deliverable_id: str) -> None:
    resp = client.get(
        f"/api/v1/deliverables/{deliverable_id}/download?format=md", headers=_OWNER
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert resp.content.decode("utf-8") == "# Hello\n\nTest content.\n"


def test_download_pdf(client: TestClient, deliverable_id: str) -> None:
    resp = client.get(
        f"/api/v1/deliverables/{deliverable_id}/download?format=pdf", headers=_OWNER
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF-")
    assert ".pdf" in resp.headers["content-disposition"]


def test_download_docx(client: TestClient, deliverable_id: str) -> None:
    resp = client.get(
        f"/api/v1/deliverables/{deliverable_id}/download?format=docx", headers=_OWNER
    )
    assert resp.status_code == 200
    assert "wordprocessingml" in resp.headers["content-type"]
    assert resp.content[:2] == b"PK"
    assert ".docx" in resp.headers["content-disposition"]


def test_download_unknown_format_422(client: TestClient, deliverable_id: str) -> None:
    resp = client.get(
        f"/api/v1/deliverables/{deliverable_id}/download?format=xyz", headers=_OWNER
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_download_nonexistent_404(client: TestClient) -> None:
    resp = client.get(
        f"/api/v1/deliverables/{uuid.uuid4()}/download", headers=_OWNER
    )
    assert resp.status_code == 404


def test_download_org_isolation(client: TestClient, deliverable_id: str) -> None:
    resp = client.get(
        f"/api/v1/deliverables/{deliverable_id}/download?format=md", headers=_OTHER
    )
    assert resp.status_code == 404


def test_download_attachment_disposition(client: TestClient, deliverable_id: str) -> None:
    resp = client.get(f"/api/v1/deliverables/{deliverable_id}/download", headers=_OWNER)
    assert "attachment" in resp.headers["content-disposition"]


def test_download_filename_sanitized(client: TestClient, deliverable_id: str) -> None:
    disp = client.get(
        f"/api/v1/deliverables/{deliverable_id}/download", headers=_OWNER
    ).headers["content-disposition"]
    assert "q3_marketing_strategy_v2" in disp
