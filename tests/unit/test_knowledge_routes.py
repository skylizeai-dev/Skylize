"""Route-level hardening for /api/v1/knowledge: oversize rejection before decode,
line-wrapped base64 acceptance, and webhook doc_id charset validation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import textwrap
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skylize.edge.routes import knowledge as kn
from skylize.schemas.base import RequestContext


class _FakeSvc:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def ingest_document(self, doc_id, content, *, source_path, org_id, department=None):
        self.calls.append(("ingest_document", doc_id, org_id))
        return 1

    async def ingest(self, doc_id, content, source_path, *, org_id, department=None):
        self.calls.append(("ingest", doc_id, org_id))

    async def search(self, *a, **k):
        return []


_WEBHOOK_SECRET = "test-webhook-secret"


class _FakeSettings:
    # The webhook is fail-closed (503 when unconfigured), so the tests configure
    # a secret and sign their requests like the real n8n caller does.
    knowledge_webhook_secret = _WEBHOOK_SECRET


def _signed(payload: dict) -> tuple[bytes, dict[str, str]]:
    """JSON-encode ``payload`` and compute its X-Hub-Signature-256 header."""
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}


class _FakeContainer:
    def __init__(self) -> None:
        self.settings = _FakeSettings()
        self.knowledge_ingestion = _FakeSvc()


def _ctx() -> RequestContext:
    return RequestContext(
        org_id="org_a",
        user_id="u1",
        roles=["member"],
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )


@pytest.fixture()
def container() -> _FakeContainer:
    return _FakeContainer()


@pytest.fixture()
def client(container: _FakeContainer) -> TestClient:
    app = FastAPI()
    app.include_router(kn.router)
    app.dependency_overrides[kn.get_container] = lambda: container
    app.dependency_overrides[kn.enforce_rate_limit] = _ctx
    return TestClient(app)


def test_upload_oversize_is_rejected_before_decode(client: TestClient, container: _FakeContainer) -> None:
    """A body over the base64 char cap 422s at request validation — never decoded."""
    oversized = "A" * (kn.MAX_CONTENT_BASE64_CHARS + 10)
    resp = client.post(
        "/api/v1/knowledge/upload",
        json={"filename": "big.txt", "content_base64": oversized},
    )
    assert resp.status_code == 422
    assert container.knowledge_ingestion.calls == []  # handler never ran


def test_upload_accepts_line_wrapped_base64(client: TestClient) -> None:
    """CLI/MIME base64 wrapped at 76 cols must decode, not spuriously 400."""
    raw = b"The onboarding handbook covers refunds, returns, and escalation paths."
    wrapped = "\n".join(textwrap.wrap(base64.b64encode(raw).decode(), 8))
    assert "\n" in wrapped
    resp = client.post(
        "/api/v1/knowledge/upload",
        json={"filename": "handbook.txt", "content_base64": wrapped},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["chunks"] == 1


def test_upload_rejects_undecodable_base64(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/knowledge/upload",
        json={"filename": "x.txt", "content_base64": "!!!not base64!!!"},
    )
    assert resp.status_code == 400


def test_ingest_webhook_rejects_non_slug_doc_id(client: TestClient) -> None:
    body, headers = _signed({"doc_id": "acme:secret", "content": "x", "source_path": "p"})
    resp = client.post("/api/v1/knowledge/ingest", content=body, headers=headers)
    assert resp.status_code == 422


def test_ingest_webhook_accepts_slug_doc_id(client: TestClient, container: _FakeContainer) -> None:
    body, headers = _signed(
        {"doc_id": "getting-started", "content": "hello", "source_path": "docs/gs.md"}
    )
    resp = client.post("/api/v1/knowledge/ingest", content=body, headers=headers)
    assert resp.status_code == 202
    assert container.knowledge_ingestion.calls == [("ingest", "getting-started", "platform")]


def test_ingest_webhook_rejects_bad_signature(client: TestClient, container: _FakeContainer) -> None:
    body, _ = _signed({"doc_id": "getting-started", "content": "hello", "source_path": "p"})
    resp = client.post(
        "/api/v1/knowledge/ingest",
        content=body,
        headers={"X-Hub-Signature-256": "sha256=" + "0" * 64, "Content-Type": "application/json"},
    )
    assert resp.status_code == 401
    assert container.knowledge_ingestion.calls == []
