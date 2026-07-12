"""Unit tests for the memory.search tool — wrapper logic against a mocked backend.

The real backend (Qdrant + OpenAI embeddings) requires live infra; here we mock
the underlying `EmbeddingService`/`QdrantAdapter` calls and verify the tool
wrapper's request/response shaping, tenant-filter injection, and the
NullMemoryRecallPort graceful-degradation path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

from skylize.tools.base import ToolContext
from skylize.tools.builtin.memory_recall import (
    MemoryHit,
    NullMemoryRecallPort,
    QdrantMemoryRecallPort,
    build_memory_recall_tool,
)

CTX = ToolContext(org_id="org_a", agent_id="hook_generator_agent", correlation_id=uuid4())


async def test_null_port_returns_no_hits() -> None:
    tool = build_memory_recall_tool(NullMemoryRecallPort())
    inp = tool.input_schema.model_validate({"query": "brand voice"})
    out = await tool.handler(inp, CTX)
    assert out.hits == []


async def test_tool_handler_passes_through_query_and_org_id() -> None:
    port = AsyncMock()
    port.recall.return_value = [MemoryHit(content="Past hook: ...", score=0.91, source="doc-1")]
    tool = build_memory_recall_tool(port)

    inp = tool.input_schema.model_validate({"query": "high-performing hooks", "top_k": 3})
    out = await tool.handler(inp, CTX)

    port.recall.assert_awaited_once_with(
        query="high-performing hooks", top_k=3, org_id="org_a", namespace=None,
    )
    assert out.hits == [MemoryHit(content="Past hook: ...", score=0.91, source="doc-1")]


async def test_tool_handler_forwards_namespace() -> None:
    port = AsyncMock()
    port.recall.return_value = []
    tool = build_memory_recall_tool(port)

    inp = tool.input_schema.model_validate({"query": "voice", "namespace": "brand:voice"})
    await tool.handler(inp, CTX)

    port.recall.assert_awaited_once_with(
        query="voice", top_k=5, org_id="org_a", namespace="brand:voice",
    )


async def test_qdrant_port_embeds_query_then_searches_with_org_filter() -> None:
    embedding_service = AsyncMock()
    embedding_service.embed.return_value = [0.1, 0.2, 0.3]
    qdrant = AsyncMock()
    qdrant.search.return_value = [
        {"score": 0.87, "content": "Prior campaign hook", "source_path": "campaigns/spring.md"},
        {"score": 0.5, "doc_id": "raw-doc-2"},
    ]

    port = QdrantMemoryRecallPort(embedding_service, qdrant)
    hits = await port.recall(query="spring campaign hooks", top_k=2, org_id="org_a", namespace=None)

    embedding_service.embed.assert_awaited_once_with("spring campaign hooks")
    qdrant.search.assert_awaited_once_with([0.1, 0.2, 0.3], 2, {"org_id": "org_a"})
    assert hits[0] == MemoryHit(content="Prior campaign hook", score=0.87, source="campaigns/spring.md")
    assert hits[1].source == "raw-doc-2"


async def test_qdrant_port_adds_namespace_to_filters() -> None:
    embedding_service = AsyncMock()
    embedding_service.embed.return_value = [0.0]
    qdrant = AsyncMock()
    qdrant.search.return_value = []

    port = QdrantMemoryRecallPort(embedding_service, qdrant)
    await port.recall(query="q", top_k=5, org_id="org_b", namespace="brand:voice")

    qdrant.search.assert_awaited_once_with([0.0], 5, {"org_id": "org_b", "namespace": "brand:voice"})


async def test_tool_id_and_category_are_stable() -> None:
    tool = build_memory_recall_tool(NullMemoryRecallPort())
    assert tool.tool_id == "memory.search"
    assert tool.category == "memory"
