"""Integration tests: /api/v1/knowledge/ingest HMAC and idempotency."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from skylize.edge.gateway import create_app


def _make_sig(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture()
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def mock_svc() -> AsyncMock:
    svc = AsyncMock()
    svc.ingest = AsyncMock(return_value=None)
    return svc


def _patch_svc(client: TestClient, svc: AsyncMock):
    client.app.state.container.knowledge_ingestion = svc


# ── HMAC tests ──────────────────────────────────────────────────────────────

def test_ingest_no_hmac_secret_returns_503(client: TestClient, mock_svc: AsyncMock) -> None:
    """Fail closed: when knowledge_webhook_secret is empty, the endpoint rejects
    with 503 rather than accepting an unverified ingest."""
    _patch_svc(client, mock_svc)
    resp = client.post(
        "/api/v1/knowledge/ingest",
        json={"doc_id": "d1", "content": "hello", "source_path": "/a.md"},
    )
    assert resp.status_code == 503
    mock_svc.ingest.assert_not_awaited()


def test_ingest_hmac_valid(client: TestClient, mock_svc: AsyncMock) -> None:
    _patch_svc(client, mock_svc)
    client.app.state.container.settings.knowledge_webhook_secret = "s3cr3t"
    payload = json.dumps({"doc_id": "d2", "content": "hi", "source_path": "/b.md"}).encode()
    sig = _make_sig(payload, "s3cr3t")
    resp = client.post(
        "/api/v1/knowledge/ingest",
        content=payload,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
    )
    assert resp.status_code == 202
    client.app.state.container.settings.knowledge_webhook_secret = ""


def test_ingest_hmac_invalid_returns_401(client: TestClient, mock_svc: AsyncMock) -> None:
    _patch_svc(client, mock_svc)
    client.app.state.container.settings.knowledge_webhook_secret = "s3cr3t"
    payload = json.dumps({"doc_id": "d3", "content": "x", "source_path": "/c.md"}).encode()
    resp = client.post(
        "/api/v1/knowledge/ingest",
        content=payload,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=deadbeef"},
    )
    assert resp.status_code == 401
    mock_svc.ingest.assert_not_awaited()
    client.app.state.container.settings.knowledge_webhook_secret = ""


def test_ingest_hmac_missing_header_returns_401(client: TestClient, mock_svc: AsyncMock) -> None:
    _patch_svc(client, mock_svc)
    client.app.state.container.settings.knowledge_webhook_secret = "s3cr3t"
    resp = client.post(
        "/api/v1/knowledge/ingest",
        json={"doc_id": "d4", "content": "y", "source_path": "/d.md"},
    )
    assert resp.status_code == 401
    mock_svc.ingest.assert_not_awaited()
    client.app.state.container.settings.knowledge_webhook_secret = ""


# ── Service not configured ───────────────────────────────────────────────────

def test_ingest_service_not_configured(client: TestClient) -> None:
    # Pass HMAC verification with a valid secret+signature so we exercise the
    # service-unconfigured 503 path, not the secret-unset 503 path.
    client.app.state.container.knowledge_ingestion = None
    client.app.state.container.settings.knowledge_webhook_secret = "s3cr3t"
    payload = json.dumps({"doc_id": "d5", "content": "z", "source_path": "/e.md"}).encode()
    sig = _make_sig(payload, "s3cr3t")
    resp = client.post(
        "/api/v1/knowledge/ingest",
        content=payload,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
    )
    assert resp.status_code == 503
    client.app.state.container.settings.knowledge_webhook_secret = ""
