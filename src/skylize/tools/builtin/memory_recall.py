"""memory.search — scoped semantic recall behind a swappable provider port.

Mirrors `web_search.py`'s port pattern: the tool depends on the narrow
`MemoryRecallPort` protocol, not on any concrete vector store, and degrades
gracefully (`NullMemoryRecallPort`) when no memory backend is configured — the
same shape as `Container.knowledge_ingestion` being `None` under the analogous
condition (bootstrap.py).

Tenant isolation is non-negotiable: the caller's `org_id` (from `ToolContext`)
is injected into the vector filter by the tool wrapper, never taken from agent
input. An optional `namespace` narrows recall further (e.g. `brand:voice`).
`QdrantMemoryRecallPort` is the first real backend (OpenAI embeddings + Qdrant);
it is the single switch point for adding another.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..base import ToolContext, ToolDefinition


class MemoryHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = ""
    score: float
    source: str = ""


class MemoryRecallIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    top_k: int = Field(default=5, gt=0, le=50)
    namespace: str | None = None


class MemoryRecallOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hits: list[MemoryHit] = Field(default_factory=list)


class MemoryRecallPort(Protocol):
    """What the tool needs from a memory backend: scoped query in, hits out."""

    async def recall(
        self, *, query: str, top_k: int, org_id: str, namespace: str | None
    ) -> list[MemoryHit]: ...


class NullMemoryRecallPort:
    """No memory backend configured — recall degrades to no hits."""

    async def recall(
        self, *, query: str, top_k: int, org_id: str, namespace: str | None
    ) -> list[MemoryHit]:
        return []


class QdrantMemoryRecallPort:
    """Real backend: embed the query, then search Qdrant under a tenant filter."""

    def __init__(self, embedding_service: Any, qdrant: Any) -> None:
        self._embeddings = embedding_service
        self._qdrant = qdrant

    async def recall(
        self, *, query: str, top_k: int, org_id: str, namespace: str | None
    ) -> list[MemoryHit]:
        vector = await self._embeddings.embed(query)
        # org_id is passed as the adapter's own scope argument (it rejects an
        # org_id smuggled inside `filters`); `filters` only ever narrows further.
        filters: dict[str, str] = {}
        if namespace is not None:
            filters["namespace"] = namespace
        results = await self._qdrant.search(vector, top_k, filters, org_id=org_id)
        return [
            MemoryHit(
                content=str(item.get("content_text") or item.get("content") or ""),
                score=float(item["score"]),
                source=str(item.get("source_path") or item.get("doc_id") or ""),
            )
            for item in results
        ]


def build_memory_recall_tool(port: MemoryRecallPort) -> ToolDefinition:
    async def _handle(inp: MemoryRecallIn, ctx: ToolContext) -> MemoryRecallOut:
        hits = await port.recall(
            query=inp.query,
            top_k=inp.top_k,
            org_id=ctx.org_id,
            namespace=inp.namespace,
        )
        return MemoryRecallOut(hits=hits)

    return ToolDefinition(
        tool_id="memory.search",
        name="Memory Recall",
        description="Recall relevant prior knowledge scoped to this tenant.",
        input_schema=MemoryRecallIn,
        output_schema=MemoryRecallOut,
        category="memory",
        handler=_handle,
    )
