"""OpenAI text-embedding-3-small wrapper with retry logic."""

from __future__ import annotations

import asyncio

import structlog
from openai import AsyncOpenAI

log = structlog.get_logger(__name__)

_MODEL = "text-embedding-3-small"
_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds


class EmbeddingService:
    def __init__(self, api_key: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key)

    async def embed(self, text: str) -> list[float]:
        last_exc: Exception | None = None
        for attempt in range(_RETRIES):
            try:
                resp = await self._client.embeddings.create(input=text, model=_MODEL)
                return resp.data[0].embedding
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                wait = _BACKOFF_BASE * (2**attempt)
                log.warning(
                    "embedding.retry",
                    attempt=attempt + 1,
                    wait_s=wait,
                    error=str(exc),
                )
                if attempt < _RETRIES - 1:
                    await asyncio.sleep(wait)
        log.error("embedding.failed", retries=_RETRIES, error=str(last_exc))
        raise RuntimeError(f"Embedding failed after {_RETRIES} attempts: {last_exc}") from last_exc
