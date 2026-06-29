"""Platform knowledge ingestion — idempotent upsert via Qdrant."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import structlog

from .embedding_service import EmbeddingService
from .qdrant_adapter import QdrantAdapter

log = structlog.get_logger(__name__)


class KnowledgeIngestionService:
    def __init__(self, qdrant: QdrantAdapter, embedding_service: EmbeddingService) -> None:
        self._qdrant = qdrant
        self._embed = embedding_service

    async def ingest(self, doc_id: str, content: str, source_path: str) -> None:
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        if await self._qdrant.verify_document(doc_id, content_hash):
            log.debug("knowledge_ingestion.skipped", doc_id=doc_id, reason="already_current")
            return
        vector = await self._embed.embed(content)
        await self._qdrant.upsert_vector(
            doc_id,
            vector,
            {
                "content_hash": content_hash,
                "source_path": source_path,
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        log.info("knowledge_ingested", doc_id=doc_id, source=source_path)
