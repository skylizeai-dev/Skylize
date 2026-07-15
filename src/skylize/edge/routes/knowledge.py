"""Platform knowledge ingestion endpoint — called by n8n HTTP Request node."""

from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from ...bootstrap import Container
from ...memory.knowledge_ingestion import KnowledgeIngestionService
from ..deps import get_container

logger = logging.getLogger(__name__)

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
    container: Container = Depends(get_container),
) -> dict[str, str]:
    # Fail closed: an unconfigured secret must NOT wave callbacks through
    # unverified (n8n.md §4 — inbound callbacks are signature-verified). Mirrors
    # the agent-prompts endpoint's 503-when-unconfigured stance.
    secret: str = getattr(container.settings, "knowledge_webhook_secret", "")
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="knowledge webhook not configured (missing SKYLIZE_KNOWLEDGE_WEBHOOK_SECRET)",
        )
    raw = await request.body()
    sig = request.headers.get("X-Hub-Signature-256")
    if not _verify_hmac(raw, sig, secret):
        # DEFERRED (audit 3aa2bed3, LOW): the documented governance model
        # (n8n.md §7, system_boundaries.md §4.6) says a rejected inbound signature
        # should emit a `governance.integration_bad_signature` GovernanceEvent.
        # Not wired yet. Correction to the prior note: the event bus IS reachable
        # here (`container.bus`) — the real blocker is that no
        # `governance.integration_bad_signature` type exists in
        # schemas/events/governance.py, so emitting it first means defining that
        # event schema (org_id?, source, remote). That is net-new and out of
        # scope for this fix; tracked as a follow-up. Until then the rejection is
        # observable via the structured warning below and still fails closed (401).
        logger.warning(
            "integration_bad_signature knowledge remote=%s",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=401, detail="invalid HMAC signature")

    svc: KnowledgeIngestionService | None = container.knowledge_ingestion
    if svc is None:
        raise HTTPException(status_code=503, detail="knowledge ingestion not configured (missing OPENAI_API_KEY or QDRANT_URL)")
    await svc.ingest(body.doc_id, body.content, body.source_path)
    return {"status": "accepted", "doc_id": body.doc_id}
