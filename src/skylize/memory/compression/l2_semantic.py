"""L2 — semantic routing (50–200ms).

Given a set of content chunks and a query/intent string, keep only the `top_k`
chunks most relevant to the query (by cosine similarity), preserving their
original order. This drops irrelevant recall before it consumes context budget.

Boundaries:
  - The embedding model is NOT loaded here. L2 depends on the injected `Embedder`
    Protocol; the concrete MiniLM adapter is a Sprint-2 deliverable that lands
    with the memory embedding service. In tests a fake Embedder is injected.
  - The embedding cache (`EmbeddingCache`, Redis-backed in prod) is optional.
    `None` means compute-through with no caching.
  - L2 MUST NOT block the pipeline on failure: any embedder/cache error is caught
    and surfaced as a degraded result so the caller can fall back to L1-only and
    emit `compression.l2_degraded`. L2 itself never raises for a routing failure.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from skylize.memory.compression.protocols import Embedder, EmbeddingCache

# 24h TTL for cached chunk embeddings (L2 spec). Chunks repeat across calls, so a
# day of reuse amortizes the embed cost heavily.
CACHE_TTL_SECONDS = 24 * 60 * 60


def chunk_cache_key(chunk: str) -> str:
    """SHA-256 hex digest of a chunk — the embedding cache key (L2 spec)."""
    return hashlib.sha256(chunk.encode("utf-8")).hexdigest()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors.

    Returns 0.0 if either vector is zero-magnitude (undefined direction) rather
    than raising — a degenerate embedding should rank last, not crash routing.
    """
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


@dataclass(frozen=True)
class L2RouteResult:
    """Outcome of an L2 routing attempt.

    `degraded` is True when routing could not complete (embedder/cache failure or
    no usable embeddings); in that case `chunks` is the unmodified input and
    `reason` explains why, so the pipeline can emit the degraded audit record.
    `selected_indices` are the original-order indices that survived selection.
    """

    chunks: list[str]
    selected_indices: list[int]
    degraded: bool
    reason: str | None = None


class L2SemanticRouter:
    """Routes content chunks by semantic relevance to a query.

    Stateless apart from its injected collaborators, so a single instance is safe
    to share across calls. The embedder is loaded once at process start by the
    caller (CPU is fine for MiniLM) and injected here.
    """

    def __init__(self, embedder: Embedder, cache: EmbeddingCache | None = None) -> None:
        self._embedder = embedder
        self._cache = cache

    def _embed_with_cache(self, texts: list[str]) -> list[list[float]]:
        """Embed `texts`, serving cache hits and back-filling misses.

        Only the cache-miss subset is sent to the embedder, in one batched call.
        With no cache injected, every text is a miss (straight compute-through).
        """
        if self._cache is None:
            return self._embedder.encode(texts)

        keys = [chunk_cache_key(t) for t in texts]
        vectors: list[list[float] | None] = [self._cache.get(k) for k in keys]

        miss_idx = [i for i, v in enumerate(vectors) if v is None]
        if miss_idx:
            fresh = self._embedder.encode([texts[i] for i in miss_idx])
            for slot, vec in zip(miss_idx, fresh, strict=True):
                vectors[slot] = vec
                self._cache.set(keys[slot], vec, CACHE_TTL_SECONDS)

        # Every slot is filled now: hits from cache, misses just computed.
        return [v for v in vectors if v is not None]

    def route(self, chunks: list[str], query: str, top_k: int) -> L2RouteResult:
        """Select the `top_k` chunks most similar to `query`, original order kept.

        Never raises: an embedder or cache failure returns a degraded result
        carrying the untouched input chunks and a reason. If there are already
        `<= top_k` chunks, routing is a no-op (all kept, in order).
        """
        if top_k <= 0:
            return L2RouteResult(chunks=[], selected_indices=[], degraded=False)
        if len(chunks) <= top_k:
            return L2RouteResult(
                chunks=list(chunks),
                selected_indices=list(range(len(chunks))),
                degraded=False,
            )

        try:
            query_vec = self._embedder.encode([query])[0]
            chunk_vecs = self._embed_with_cache(chunks)
        except Exception as exc:  # noqa: BLE001 — degrade on ANY embedding failure
            return L2RouteResult(
                chunks=list(chunks),
                selected_indices=list(range(len(chunks))),
                degraded=True,
                reason=f"embedding failed: {type(exc).__name__}: {exc}",
            )

        scored = [
            (idx, cosine_similarity(query_vec, vec))
            for idx, vec in enumerate(chunk_vecs)
        ]
        # Top_k by score (desc), then restore original order so the surviving
        # chunks read in the sequence the caller assembled them.
        top = sorted(scored, key=lambda pair: pair[1], reverse=True)[:top_k]
        kept_indices = sorted(idx for idx, _ in top)
        return L2RouteResult(
            chunks=[chunks[i] for i in kept_indices],
            selected_indices=kept_indices,
            degraded=False,
        )
