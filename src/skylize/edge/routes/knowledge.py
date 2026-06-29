"""Platform knowledge ingestion endpoint — called by n8n HTTP Request node."""

from __future__ import annotations

import hashlib
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from ...memory.knowledge_ingestion import KnowledgeIngestionService
from ..deps import get_container

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str
    content: str
    source_path: str


def _verify_hmac(body: bytes, signature_header: str | None, secret: str) -> bool:
    if not signature_header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


@router.post("/ingest", status_code=202)
async def ingest_knowledge(
    request: Request,
    body: IngestRequest,
    container=Depends(get_container),
) -> dict[str, str]:
    secret: str = getattr(container.settings, "knowledge_webhook_secret", "")
    if secret:
        raw = await request.body()
        sig = request.headers.get("X-Hub-Signature-256")
        if not _verify_hmac(raw, sig, secret):
            raise HTTPException(status_code=403, detail="invalid HMAC signature")

    svc: KnowledgeIngestionService | None = container.knowledge_ingestion
    if svc is None:
        raise HTTPException(status_code=503, detail="knowledge ingestion not configured (missing OPENAI_API_KEY or QDRANT_URL)")
    await svc.ingest(body.doc_id, body.content, body.source_path)
    return {"status": "accepted", "doc_id": body.doc_id}
