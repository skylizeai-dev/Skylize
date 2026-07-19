"""Knowledge routes — platform docs webhook + org-scoped ingestion/search.

The /ingest webhook (n8n docs sync) writes under the shared "platform" tenant
(passed explicitly). /upload, /interview and /search operate strictly inside the
caller's org_id from the authenticated request context — the tenant isolation
boundary lives in KnowledgeIngestionService.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ...bootstrap import Container
from ...memory import identity
from ...memory.extraction import UnsupportedFormatError, extract_text, infer_department
from ...memory.knowledge_ingestion import PLATFORM_ORG, KnowledgeIngestionService
from ...schemas.base import RequestContext
from ..deps import enforce_rate_limit, get_container

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])

MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # decoded size cap
# base64 encodes 3 input bytes as 4 chars, so N bytes -> ceil(N/3)*4 chars.
# Bound the *encoded* string length so an oversized body is rejected by request
# validation BEFORE we allocate the decoded bytes. Allow ~2% slack for line-wrap
# whitespace (CLI/MIME wrap at 76 cols), which we strip before decoding.
_MAX_B64_CHARS_UNWRAPPED = (MAX_UPLOAD_BYTES + 2) // 3 * 4
MAX_CONTENT_BASE64_CHARS = int(_MAX_B64_CHARS_UNWRAPPED * 1.02)


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str
    content: str
    source_path: str


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=MAX_CONTENT_BASE64_CHARS)
    department: str | None = None


class UploadResponse(BaseModel):
    doc_id: str
    department: str
    chunks: int
    characters: int


class InterviewAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=10_000)
    department: str | None = None


class InterviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: list[InterviewAnswer] = Field(min_length=1, max_length=20)


class InterviewResponse(BaseModel):
    ingested: int


class SearchHit(BaseModel):
    score: float
    source_path: str | None = None
    department: str | None = None
    content_text: str | None = None


class SearchResponse(BaseModel):
    results: list[SearchHit]


class _InvalidBase64(Exception):
    """content_base64 was not decodable even after whitespace was stripped."""


class _UploadTooLarge(Exception):
    """Decoded upload exceeded MAX_UPLOAD_BYTES."""


def _verify_hmac(body: bytes, signature_header: str | None, secret: str) -> bool:
    if not signature_header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _svc(container: Container) -> KnowledgeIngestionService:
    svc: KnowledgeIngestionService | None = container.knowledge_ingestion
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail="knowledge ingestion not configured (missing OPENAI_API_KEY or QDRANT_URL)",
        )
    return svc


def _decode_and_extract(filename: str, content_base64: str) -> str:
    """CPU-bound: strip whitespace, base64-decode, size-check, extract text.

    Runs in a worker thread (see the /upload handler) so a large PDF/docx parse
    never blocks the single-worker event loop.
    """
    cleaned = "".join(content_base64.split())  # tolerate CLI/MIME line wrapping
    try:
        data = base64.b64decode(cleaned, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _InvalidBase64 from exc
    if len(data) > MAX_UPLOAD_BYTES:
        raise _UploadTooLarge
    return extract_text(filename, data)


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

    try:
        identity.validate_identifier(body.doc_id, field="doc_id")
    except identity.InvalidIdentifier as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    svc = _svc(container)
    # platform docs live under the shared "platform" tenant (passed explicitly).
    await svc.ingest(body.doc_id, body.content, body.source_path, org_id=PLATFORM_ORG)
    return {"status": "accepted", "doc_id": body.doc_id}


@router.post("/upload", response_model=UploadResponse, status_code=201)
async def upload_knowledge(
    body: UploadRequest,
    ctx: RequestContext = Depends(enforce_rate_limit),
    container: Container = Depends(get_container),
) -> UploadResponse:
    svc = _svc(container)
    try:
        text = await asyncio.to_thread(
            _decode_and_extract, body.filename, body.content_base64
        )
    except _InvalidBase64:
        raise HTTPException(status_code=400, detail="content_base64 is not valid base64")
    except _UploadTooLarge:
        raise HTTPException(status_code=413, detail="file exceeds the 15 MB upload limit")
    except UnsupportedFormatError as exc:
        raise HTTPException(status_code=415, detail=str(exc))
    if not text.strip():
        raise HTTPException(status_code=422, detail="no extractable text found in the file")

    department = body.department or infer_department(text)
    # Content-derived doc_id: identical files dedup; two same-second uploads of
    # different files never collide (finding #4 / #7).
    doc_id = identity.content_doc_id(text.encode("utf-8"), prefix="upload")
    chunks = await svc.ingest_document(
        doc_id,
        text,
        source_path=body.filename,
        org_id=ctx.org_id,
        department=department,
    )
    return UploadResponse(
        doc_id=doc_id, department=department, chunks=chunks, characters=len(text)
    )


@router.post("/interview", response_model=InterviewResponse, status_code=201)
async def interview_knowledge(
    body: InterviewRequest,
    ctx: RequestContext = Depends(enforce_rate_limit),
    container: Container = Depends(get_container),
) -> InterviewResponse:
    svc = _svc(container)
    for item in body.answers:
        text = f"Q: {item.question}\nA: {item.answer}"
        department = item.department or infer_department(item.answer)
        doc_id = identity.content_doc_id(text.encode("utf-8"), prefix="interview")
        await svc.ingest_document(
            doc_id,
            text,
            source_path="onboarding-interview",
            org_id=ctx.org_id,
            department=department,
        )
    return InterviewResponse(ingested=len(body.answers))


@router.get("/search", response_model=SearchResponse)
async def search_knowledge(
    q: str,
    department: str | None = None,
    ctx: RequestContext = Depends(enforce_rate_limit),
    container: Container = Depends(get_container),
) -> SearchResponse:
    svc = _svc(container)
    hits = await svc.search(q, org_id=ctx.org_id, top_k=8, department=department)
    results: list[SearchHit] = []
    for h in hits:
        score = h.get("score")
        source_path = h.get("source_path")
        dept = h.get("department")
        content_text = h.get("content_text")
        results.append(
            SearchHit(
                score=float(score) if isinstance(score, (int, float)) else 0.0,
                source_path=source_path if isinstance(source_path, str) else None,
                department=dept if isinstance(dept, str) else None,
                content_text=content_text[:500] if isinstance(content_text, str) and content_text else None,
            )
        )
    return SearchResponse(results=results)
