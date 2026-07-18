"""Qdrant vector store adapter for platform knowledge."""

from __future__ import annotations

import hashlib

import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    UpdateStatus,
    VectorParams,
)

log = structlog.get_logger(__name__)

_COLLECTION = "platform_knowledge"
_DIMENSION = 1536


class QdrantAdapter:
    def __init__(self, qdrant_url: str, qdrant_api_key: str = "") -> None:
        self._client = AsyncQdrantClient(
            url=qdrant_url,
            api_key=qdrant_api_key or None,
        )

    async def _ensure_collection(self) -> None:
        existing = {c.name for c in (await self._client.get_collections()).collections}
        if _COLLECTION not in existing:
            await self._client.create_collection(
                collection_name=_COLLECTION,
                vectors_config=VectorParams(size=_DIMENSION, distance=Distance.COSINE),
            )

    async def upsert_document(
        self, doc_id: str, content: str, metadata: dict[str, object]
    ) -> None:
        raise TypeError(
            "upsert_document requires a pre-computed vector; call upsert_vector instead"
        )

    async def upsert_vector(
        self, doc_id: str, vector: list[float], metadata: dict[str, object]
    ) -> None:
        await self._ensure_collection()
        result = await self._client.upsert(
            collection_name=_COLLECTION,
            points=[PointStruct(id=_stable_id(doc_id), vector=vector, payload={"doc_id": doc_id, **metadata})],
        )
        if result.status != UpdateStatus.COMPLETED:
            raise RuntimeError(f"Qdrant upsert failed: {result.status}")
        log.debug("qdrant.upserted", doc_id=doc_id)

    async def search(
        self, query_vector: list[float], top_k: int, filters: dict[str, object]
    ) -> list[dict[str, object]]:
        await self._ensure_collection()
        qdrant_filter: Filter | None = None
        if filters:
            qdrant_filter = Filter(
                must=[
                    FieldCondition(key=k, match=MatchValue(value=v))
                    for k, v in filters.items()
                ]
            )
        hits = await self._client.search(  # type: ignore[attr-defined]  # qdrant-client API drift; recall path unwired (dead code, no tracked removal plan)
            collection_name=_COLLECTION,
            query_vector=query_vector,
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=True,
        )
        return [{"score": h.score, **(h.payload or {})} for h in hits]

    async def verify_document(self, doc_id: str, content_hash: str) -> bool:
        """Return True if doc_id exists with matching content_hash (already current)."""
        await self._ensure_collection()
        results = await self._client.retrieve(
            collection_name=_COLLECTION,
            ids=[_stable_id(doc_id)],
            with_payload=True,
        )
        if not results:
            return False
        payload = results[0].payload or {}
        return payload.get("content_hash") == content_hash


def _stable_id(doc_id: str) -> str:
    """Map arbitrary doc_id string to a UUID-format string Qdrant accepts."""
    import uuid
    return str(uuid.UUID(bytes=hashlib.md5(doc_id.encode()).digest()))
