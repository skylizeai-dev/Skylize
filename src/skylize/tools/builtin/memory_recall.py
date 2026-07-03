"""memory.search — recall from the platform-knowledge Qdrant store.

The tool depends on the narrow `MemoryRecallPort` protocol, not on Qdrant or
OpenAI directly, so it stays unit-testable and degrades gracefully
(`NullMemoryRecallPort`) when no vector backend is configured — mirrors
`Container.knowledge_ingestion` being `None` under the same condition
(bootstrap.py).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..base import ToolContext, ToolDefinition

if TYPE_CHECKING:
    from ...memory.embedding_service import EmbeddingService
    from ...memory.qdrant_adapter import QdrantAdapter


class MemorySearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    top_k: int = Field(default=5, gt=0, le=20)
    namespace: str | None = None


class MemoryHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    score: float
    source: str | None = None


class MemorySearchOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hits: list[MemoryHit] = Field(default_factory=list)


class MemoryRecallPort(Protocol):
    """What the tool needs from the memory backend: embed + tenant-scoped search."""

    async def recall(
        self, *, query: str, top_k: int, org_id: str, namespace: str | None
    ) -> list[MemoryHit]: ...


class NullMemoryRecallPort:
    """No vector backend configured (no SKYLIZE_QDRANT_URL / OPENAI_API_KEY)."""

    async def recall(
        self, *, query: str, top_k: int, org_id: str, namespace: str | None
    ) -> list[MemoryHit]:
        return []


class QdrantMemoryRecallPort:
    """Real backend: embeds the query, then searches `platform_knowledge`.

    Every search is filtered by `org_id` (IF-DATA invariant: tenant isolation
    enforced at the Data Boundary regardless of any upstream check).
    """

    def __init__(self, embedding_service: "EmbeddingService", qdrant: "QdrantAdapter") -> None:
        self._embed = embedding_service
        self._qdrant = qdrant

    async def recall(
        self, *, query: str, top_k: int, org_id: str, namespace: str | None
    ) -> list[MemoryHit]:
        vector = await self._embed.embed(query)
        filters: dict[str, Any] = {"org_id": org_id}
        if namespace:
            filters["namespace"] = namespace
        hits = await self._qdrant.search(vector, top_k, filters)
        return [
            MemoryHit(
                content=str(hit.get("content_text") or hit.get("source_path") or ""),
                score=float(hit.get("score", 0.0)),
                source=hit.get("source_path") or hit.get("doc_id"),
            )
            for hit in hits
        ]


def build_memory_recall_tool(port: MemoryRecallPort) -> ToolDefinition:
    async def _handle(inp: MemorySearchIn, ctx: ToolContext) -> MemorySearchOut:
        hits = await port.recall(
            query=inp.query, top_k=inp.top_k, org_id=ctx.org_id, namespace=inp.namespace,
        )
        return MemorySearchOut(hits=hits)

    return ToolDefinition(
        tool_id="memory.search",
        name="Memory Search",
        description=(
            "Semantic search over this organization's platform knowledge memory. "
            "Use this to recall prior brand voice, past high-performing patterns, "
            "or other stored context before generating new content."
        ),
        input_schema=MemorySearchIn,
        output_schema=MemorySearchOut,
        category="memory",
        handler=_handle,
    )
