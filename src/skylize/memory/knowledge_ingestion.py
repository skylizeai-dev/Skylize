"""Org-scoped knowledge ingestion — injective tenant identity + idempotent upsert.

MULTI-TENANT ISOLATION: every write is keyed by an injective ``(org_id, doc_id)``
point id (see ``memory.identity.point_id``) and tagged with ``org_id``; every
read filters on ``org_id``. A document ingested under tenant A is unreachable
from any tenant-B query — enforced here at the service boundary, not in callers.

``org_id`` is REQUIRED: there is no implicit shared-tenant default, so a caller
cannot silently leak data into a shared namespace by forgetting the argument.
``PLATFORM_ORG`` is passed EXPLICITLY by the n8n docs webhook; no read path
includes it (platform-docs read access is a later sprint).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import structlog
from pydantic import BaseModel, ConfigDict

from ..adapters.llm.content_gate import LLMContentGate
from . import identity
from .embedding_service import EmbeddingService
from .qdrant_adapter import QdrantAdapter, QdrantPoint

log = structlog.get_logger(__name__)

PLATFORM_ORG = "platform"

# ~1500 chars per chunk keeps well inside embedding context while preserving
# paragraph-scale meaning; 200-char overlap avoids cutting facts at borders.
CHUNK_SIZE = 1500
CHUNK_OVERLAP = 200


def chunk_text(content: str) -> list[str]:
    """Split content into overlapping chunks on paragraph boundaries where possible."""
    text = content.strip()
    if not text:
        return []
    if len(text) <= CHUNK_SIZE:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        if end < len(text):
            # prefer to break at a paragraph, else sentence, else hard cut
            for sep in ("\n\n", "\n", ". "):
                cut = text.rfind(sep, start + CHUNK_SIZE // 2, end)
                if cut != -1:
                    end = cut + len(sep)
                    break
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return [c for c in chunks if c]


class KnowledgePayload(BaseModel):
    """Validated Qdrant payload for a knowledge vector (no raw dicts cross the
    service→adapter boundary)."""

    model_config = ConfigDict(extra="forbid")

    org_id: str
    doc_id: str
    parent_doc_id: str
    chunk_index: int | None
    department: str | None
    content_hash: str
    doc_content_hash: str
    source_path: str
    content_text: str
    ingested_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class KnowledgeIngestionService:
    def __init__(
        self,
        qdrant: QdrantAdapter,
        embedding_service: EmbeddingService,
        content_gate: LLMContentGate | None = None,
    ) -> None:
        self._qdrant = qdrant
        self._embed = embedding_service
        # Ingested docs land in `platform_knowledge` and are later retrieved
        # into agent context — an indirect-injection vector, so screen the raw
        # content before it is embedded/stored, not just at generation time.
        self._gate = content_gate or LLMContentGate()

    async def ingest(
        self,
        doc_id: str,
        content: str,
        source_path: str,
        *,
        org_id: str,
        department: str | None = None,
    ) -> None:
        """Single-vector upsert of a whole document (webhook + deliverables path)."""
        # Indirect-injection screen: raw content is embedded then later retrieved
        # into agent context, so it MUST pass the gate before any embed/upsert.
        # Screened first, ahead of the idempotency check, so no path reaches the
        # store unscreened.
        self._gate.check(content)
        pid = identity.point_id(org_id, doc_id)
        content_hash = _sha256(content)
        if await self._qdrant.verify_point(pid, content_hash):
            log.debug(
                "knowledge_ingestion.skipped",
                org_id=org_id,
                doc_id=doc_id,
                reason="already_current",
            )
            return
        vector = await self._embed.embed(content)
        payload = KnowledgePayload(
            org_id=org_id,
            doc_id=doc_id,
            parent_doc_id=doc_id,
            chunk_index=None,
            department=department,
            content_hash=content_hash,
            doc_content_hash=content_hash,
            source_path=source_path,
            content_text=content,
            ingested_at=_now_iso(),
        )
        await self._qdrant.upsert_points(
            [QdrantPoint(point_id=pid, vector=vector, payload=payload.model_dump())]
        )
        log.info("knowledge_ingested", org_id=org_id, doc_id=doc_id, source=source_path)

    async def ingest_document(
        self,
        doc_id: str,
        content: str,
        source_path: str,
        *,
        org_id: str,
        department: str | None = None,
    ) -> int:
        """Chunk → batch embed → tenant-scoped write. Returns the chunk count.

        Idempotent per document: if ``doc_id`` already holds this exact content
        it re-embeds nothing. On a content change it purges the document's prior
        chunks first, so a shorter re-ingest cannot leave stale higher-index
        chunks live and searchable.
        """
        # Indirect-injection screen: screen the whole raw document before it is
        # chunked/embedded/upserted, so no chunk can reach the store unscreened
        # (an injection that straddles a chunk boundary is still caught).
        self._gate.check(content)
        chunks = chunk_text(content)
        if not chunks:
            return 0
        doc_hash = _sha256(content)
        # doc-level idempotency: chunk 0 carries the whole-document hash.
        chunk0_pid = identity.chunk_point_id(org_id, doc_id, 0)
        if await self._qdrant.point_doc_hash(chunk0_pid) == doc_hash:
            log.debug("knowledge_ingestion.doc_unchanged", org_id=org_id, doc_id=doc_id)
            return len(chunks)
        # content changed (or new): purge any prior chunks of this document.
        await self._qdrant.delete_by_filter({"org_id": org_id, "parent_doc_id": doc_id})
        vectors = await self._embed.embed_batch(chunks)
        now = _now_iso()
        points: list[QdrantPoint] = []
        for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
            payload = KnowledgePayload(
                org_id=org_id,
                doc_id=identity.chunk_doc_id(doc_id, i),
                parent_doc_id=doc_id,
                chunk_index=i,
                department=department,
                content_hash=_sha256(chunk),
                doc_content_hash=doc_hash,
                source_path=source_path,
                content_text=chunk,
                ingested_at=now,
            )
            points.append(
                QdrantPoint(
                    point_id=identity.chunk_point_id(org_id, doc_id, i),
                    vector=vector,
                    payload=payload.model_dump(),
                )
            )
        await self._qdrant.upsert_points(points)
        log.info(
            "knowledge_document_ingested",
            org_id=org_id,
            doc_id=doc_id,
            chunks=len(points),
            source=source_path,
        )
        return len(chunks)

    async def search(
        self,
        query: str,
        *,
        org_id: str,
        top_k: int = 5,
        department: str | None = None,
    ) -> list[dict[str, object]]:
        """Org-scoped semantic search. The org_id filter is non-optional."""
        vector = await self._embed.embed(query)
        filters: dict[str, object] = {"org_id": org_id}
        if department is not None:
            filters["department"] = department
        return await self._qdrant.search(vector, top_k, filters)
