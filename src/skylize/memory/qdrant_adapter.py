"""Qdrant vector store adapter for platform knowledge.

Two write paths share this adapter:

* ``upsert_vector`` / ``verify_document`` — the legacy md5-keyed path used by the
  MemoryService index (out of scope for the tenant-identity remediation; kept
  byte-compatible so that subsystem is untouched).
* ``upsert_points`` / ``verify_point`` / ``point_doc_hash`` / ``delete_by_filter``
  — the tenant-injective knowledge path. Callers compute an injective point id
  via ``memory.identity`` and pass it explicitly; the adapter never derives it.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointStruct,
    UpdateStatus,
    VectorParams,
)

log = structlog.get_logger(__name__)

_COLLECTION = "platform_knowledge"
_DIMENSION = 1536
_UPSERT_BATCH = 256  # points per Qdrant upsert request


class QdrantPoint(BaseModel):
    """A vector point with a caller-computed, tenant-injective point id."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    point_id: str
    vector: list[float]
    payload: dict[str, Any]


class QdrantAdapter:
    def __init__(self, qdrant_url: str, qdrant_api_key: str = "") -> None:
        self._client = AsyncQdrantClient(
            url=qdrant_url,
            api_key=qdrant_api_key or None,
        )
        # Collections are immortal once created; memoize so the hot ingest loop
        # doesn't pay a get_collections round-trip on every point.
        self._collection_ready = False

    async def _ensure_collection(self) -> None:
        if self._collection_ready:
            return
        existing = {c.name for c in (await self._client.get_collections()).collections}
        if _COLLECTION not in existing:
            await self._client.create_collection(
                collection_name=_COLLECTION,
                vectors_config=VectorParams(size=_DIMENSION, distance=Distance.COSINE),
            )
        self._collection_ready = True

    # ── legacy single-write path (MemoryService index) ──────────────────────
    async def upsert_vector(
        self, doc_id: str, vector: list[float], metadata: dict
    ) -> None:
        await self._ensure_collection()
        result = await self._client.upsert(
            collection_name=_COLLECTION,
            points=[
                PointStruct(
                    id=_stable_id(doc_id),
                    vector=vector,
                    payload={"doc_id": doc_id, **metadata},
                )
            ],
        )
        if result.status != UpdateStatus.COMPLETED:
            raise RuntimeError(f"Qdrant upsert failed: {result.status}")
        log.debug("qdrant.upserted", doc_id=doc_id)

    async def verify_document(self, doc_id: str, content_hash: str) -> bool:
        """Legacy md5-keyed existence check (MemoryService / older callers)."""
        await self._ensure_collection()
        results = await self._client.retrieve(
            collection_name=_COLLECTION,
            ids=[_stable_id(doc_id)],
            with_payload=True,
        )
        if not results:
            return False
        return (results[0].payload or {}).get("content_hash") == content_hash

    # ── tenant-injective knowledge path (explicit point ids) ────────────────
    async def upsert_points(self, points: list[QdrantPoint]) -> None:
        """Batch upsert of vectors under caller-supplied injective point ids."""
        if not points:
            return
        await self._ensure_collection()
        for start in range(0, len(points), _UPSERT_BATCH):
            batch = points[start : start + _UPSERT_BATCH]
            result = await self._client.upsert(
                collection_name=_COLLECTION,
                points=[
                    PointStruct(id=p.point_id, vector=p.vector, payload=p.payload)
                    for p in batch
                ],
            )
            if result.status != UpdateStatus.COMPLETED:
                raise RuntimeError(f"Qdrant batch upsert failed: {result.status}")

    async def verify_point(self, point_id: str, content_hash: str) -> bool:
        """True if ``point_id`` exists with a matching ``content_hash``."""
        await self._ensure_collection()
        results = await self._client.retrieve(
            collection_name=_COLLECTION, ids=[point_id], with_payload=True
        )
        if not results:
            return False
        return (results[0].payload or {}).get("content_hash") == content_hash

    async def point_doc_hash(self, point_id: str) -> str | None:
        """The whole-document hash stored on ``point_id`` (None if absent)."""
        await self._ensure_collection()
        results = await self._client.retrieve(
            collection_name=_COLLECTION, ids=[point_id], with_payload=True
        )
        if not results:
            return None
        return (results[0].payload or {}).get("doc_content_hash")

    async def delete_by_filter(self, filters: dict[str, Any]) -> None:
        """Delete every point whose payload matches all ``filters`` (exact match)."""
        await self._ensure_collection()
        condition = Filter(
            must=[
                FieldCondition(key=k, match=MatchValue(value=v))
                for k, v in filters.items()
            ]
        )
        result = await self._client.delete(
            collection_name=_COLLECTION,
            points_selector=FilterSelector(filter=condition),
        )
        if result.status != UpdateStatus.COMPLETED:
            raise RuntimeError(f"Qdrant delete failed: {result.status}")

    async def search(
        self, query_vector: list[float], top_k: int, filters: dict
    ) -> list[dict]:
        await self._ensure_collection()
        qdrant_filter: Filter | None = None
        if filters:
            qdrant_filter = Filter(
                must=[
                    FieldCondition(key=k, match=MatchValue(value=v))
                    for k, v in filters.items()
                ]
            )
        hits = await self._client.search(
            collection_name=_COLLECTION,
            query_vector=query_vector,
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=True,
        )
        return [{"score": h.score, **(h.payload or {})} for h in hits]


def _stable_id(doc_id: str) -> str:
    """Legacy md5→UUID id derivation, retained for the MemoryService index path
    (out of scope for the tenant-identity remediation). Knowledge writes use the
    injective ``memory.identity.point_id`` instead of this function."""
    return str(uuid.UUID(bytes=hashlib.md5(doc_id.encode()).digest()))
