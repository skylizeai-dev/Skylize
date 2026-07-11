from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger(__name__)


class IngestResult:
    OK = "ok"
    UNCONFIGURED = "unconfigured"  # 503 — ingestion not wired yet


def sign_payload(body: bytes, secret: str) -> str:
    """Return X-Hub-Signature-256 header value; mirrors server _verify_hmac exactly."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        # 503 is NOT retried here — it's handled as a soft failure (UNCONFIGURED).
        # 403 is NOT retried — it is an auth error that won't change on retry.
        return exc.response.status_code in {429, 500, 502, 504}
    return isinstance(exc, (httpx.ConnectError, httpx.TimeoutException))


class SkylizeIngestClient:
    def __init__(self, base_url: str, webhook_secret: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)
        self._secret = webhook_secret

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    async def ingest(self, doc_id: str, content: str, source_path: str) -> str:
        payload = {"doc_id": doc_id, "content": content, "source_path": source_path}
        # Serialize once; hash those exact bytes; send those same bytes.
        body_bytes = json.dumps(payload).encode("utf-8")

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._secret:
            headers["X-Hub-Signature-256"] = sign_payload(body_bytes, self._secret)

        r = await self._client.post(
            "/api/v1/knowledge/ingest",
            content=body_bytes,
            headers=headers,
        )

        if r.status_code == 202:
            log.info("ingest.accepted", doc_id=doc_id)
            return IngestResult.OK

        if r.status_code == 503:
            log.warning(
                "ingest.unconfigured",
                doc_id=doc_id,
                detail="ingestion not configured, OpenAI key pending",
            )
            return IngestResult.UNCONFIGURED

        if r.status_code == 403:
            log.error("ingest.auth_error", doc_id=doc_id, status=403)

        # Raises httpx.HTTPStatusError — tenacity will retry only for retryable codes.
        r.raise_for_status()
        return IngestResult.OK  # unreachable; satisfies type checker

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> SkylizeIngestClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
