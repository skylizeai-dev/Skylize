"""OpenAI text-embedding-3-small wrapper with retry logic + batch embedding."""

from __future__ import annotations

import asyncio

import structlog
from openai import AsyncOpenAI

log = structlog.get_logger(__name__)

_MODEL = "text-embedding-3-small"
_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds
_MAX_BATCH = 128  # inputs per embeddings.create call (well under the API limit)


class EmbeddingService:
    def __init__(self, api_key: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key)

    async def _create(self, payload: str | list[str]):
        """One embeddings.create call with bounded exponential-backoff retry."""
        last_exc: Exception | None = None
        for attempt in range(_RETRIES):
            try:
                return await self._client.embeddings.create(input=payload, model=_MODEL)
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
        raise RuntimeError(
            f"Embedding failed after {_RETRIES} attempts: {last_exc}"
        ) from last_exc

    async def embed(self, text: str) -> list[float]:
        resp = await self._create(text)
        return resp.data[0].embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed many texts, one API call per ``_MAX_BATCH`` group, order preserved.

        Replaces N sequential single-input calls with ⌈N/128⌉ batched calls —
        the difference between tens of thousands of round-trips and a few dozen
        for a large document.
        """
        out: list[list[float]] = []
        for start in range(0, len(texts), _MAX_BATCH):
            group = texts[start : start + _MAX_BATCH]
            resp = await self._create(group)
            # OpenAI may return data out of order; sort by index to be safe.
            ordered = sorted(resp.data, key=lambda d: d.index)
            out.extend(d.embedding for d in ordered)
        return out
