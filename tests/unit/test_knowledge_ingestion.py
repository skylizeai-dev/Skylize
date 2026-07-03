"""Unit tests: KnowledgeIngestionService idempotency (single-vector path)."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock

import pytest

from skylize.memory import identity
from skylize.memory.knowledge_ingestion import KnowledgeIngestionService


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


@pytest.fixture()
def qdrant() -> AsyncMock:
    mock = AsyncMock()
    mock.verify_point = AsyncMock(return_value=False)
    mock.upsert_points = AsyncMock(return_value=None)
    return mock


@pytest.fixture()
def embedder() -> AsyncMock:
    mock = AsyncMock()
    mock.embed = AsyncMock(return_value=[0.1] * 1536)
    return mock


@pytest.fixture()
def svc(qdrant: AsyncMock, embedder: AsyncMock) -> KnowledgeIngestionService:
    return KnowledgeIngestionService(qdrant=qdrant, embedding_service=embedder)


async def test_ingest_calls_upsert(
    svc: KnowledgeIngestionService, qdrant: AsyncMock, embedder: AsyncMock
) -> None:
    await svc.ingest("doc-1", "hello world", "/notes/hello.md", org_id="platform")
    embedder.embed.assert_awaited_once_with("hello world")
    qdrant.upsert_points.assert_awaited_once()

    (points,), _ = qdrant.upsert_points.call_args
    assert len(points) == 1
    point = points[0]
    # writes are tenant-namespaced via an injective point id
    assert point.point_id == identity.point_id("platform", "doc-1")
    assert point.payload["org_id"] == "platform"
    assert point.payload["content_hash"] == _hash("hello world")
    assert point.payload["source_path"] == "/notes/hello.md"
    assert point.payload["content_text"] == "hello world"


async def test_ingest_idempotent_skip(
    svc: KnowledgeIngestionService, qdrant: AsyncMock, embedder: AsyncMock
) -> None:
    """Second ingest of identical content must be a no-op."""
    qdrant.verify_point = AsyncMock(return_value=True)
    await svc.ingest("doc-1", "hello world", "/notes/hello.md", org_id="platform")
    embedder.embed.assert_not_awaited()
    qdrant.upsert_points.assert_not_awaited()


async def test_ingest_reingest_on_changed_content(
    svc: KnowledgeIngestionService, qdrant: AsyncMock, embedder: AsyncMock
) -> None:
    """Different content hash → upsert must run."""
    qdrant.verify_point = AsyncMock(return_value=False)
    await svc.ingest("doc-1", "updated content", "/notes/hello.md", org_id="platform")
    embedder.embed.assert_awaited_once()
    qdrant.upsert_points.assert_awaited_once()
