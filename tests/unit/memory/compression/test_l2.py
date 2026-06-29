"""Unit tests for L2 semantic routing.

Exercises routing against a fake `Embedder` (no model loaded) and a fake
`EmbeddingCache`: top_k selection, original-order preservation, the cache
hit/miss/back-fill path, and the non-blocking degraded fallback when the embedder
raises. A latency check confirms top-5 over 50 chunks stays under the 200ms
budget against a trivial embedder.
"""

from __future__ import annotations

import time

import pytest

from skylize.memory.compression.l2_semantic import (
    CACHE_TTL_SECONDS,
    L2SemanticRouter,
    chunk_cache_key,
    cosine_similarity,
)
from skylize.memory.compression.protocols import Embedder, EmbeddingCache


class KeywordEmbedder:
    """Deterministic toy embedder: a 2-d vector marking presence of 'match'.

    A text containing the token 'match' embeds toward axis 0; everything else
    toward axis 1. Lets tests assert exactly which chunks should rank highest for
    a query that itself contains 'match'.
    """

    def __init__(self) -> None:
        self.encode_calls: list[list[str]] = []

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.encode_calls.append(list(texts))
        return [[1.0, 0.0] if "match" in t else [0.0, 1.0] for t in texts]


class ExplodingEmbedder:
    """Embedder that always raises — drives the degraded path."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("model OOM")


class DictCache:
    """In-memory EmbeddingCache double recording get/set traffic."""

    def __init__(self) -> None:
        self.store: dict[str, list[float]] = {}
        self.get_keys: list[str] = []
        self.set_calls: list[tuple[str, list[float], int]] = []

    def get(self, key: str) -> list[float] | None:
        self.get_keys.append(key)
        return self.store.get(key)

    def set(self, key: str, value: list[float], ttl_seconds: int) -> None:
        self.set_calls.append((key, value, ttl_seconds))
        self.store[key] = value


def test_doubles_satisfy_protocols() -> None:
    assert isinstance(KeywordEmbedder(), Embedder)
    assert isinstance(DictCache(), EmbeddingCache)


class TestCosineSimilarity:
    def test_identical_vectors(self) -> None:
        assert cosine_similarity([1.0, 2.0], [1.0, 2.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self) -> None:
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_zero_vector_returns_zero_not_error(self) -> None:
        assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


class TestRouting:
    def test_keeps_all_when_fewer_than_top_k(self) -> None:
        router = L2SemanticRouter(KeywordEmbedder())
        result = router.route(["a", "b"], query="match", top_k=5)
        assert result.degraded is False
        assert result.chunks == ["a", "b"]
        assert result.selected_indices == [0, 1]

    def test_selects_top_k_by_similarity(self) -> None:
        router = L2SemanticRouter(KeywordEmbedder())
        chunks = ["noise one", "match alpha", "noise two", "match beta", "noise three"]
        result = router.route(chunks, query="match", top_k=2)
        # The two 'match' chunks score highest.
        assert result.chunks == ["match alpha", "match beta"]

    def test_preserves_original_order(self) -> None:
        router = L2SemanticRouter(KeywordEmbedder())
        # 'match' chunks at indices 3 then 1 (out of natural order by score path);
        # output must restore ascending original order.
        chunks = ["match late", "noise", "noise", "match early-but-later-index"]
        result = router.route(chunks, query="match", top_k=2)
        assert result.selected_indices == sorted(result.selected_indices)
        assert result.chunks == ["match late", "match early-but-later-index"]

    def test_zero_top_k_returns_empty(self) -> None:
        router = L2SemanticRouter(KeywordEmbedder())
        result = router.route(["a", "b", "c"], query="match", top_k=0)
        assert result.chunks == []
        assert result.degraded is False


class TestCache:
    def test_cache_miss_then_set_with_ttl(self) -> None:
        cache = DictCache()
        embedder = KeywordEmbedder()
        router = L2SemanticRouter(embedder, cache=cache)
        chunks = ["match a", "noise b", "noise c", "match d", "noise e"]
        router.route(chunks, query="match", top_k=2)

        # Every chunk was a miss → set once each with the 24h TTL.
        assert len(cache.set_calls) == len(chunks)
        assert all(ttl == CACHE_TTL_SECONDS for _, _, ttl in cache.set_calls)
        assert all(ttl == 24 * 60 * 60 for _, _, ttl in cache.set_calls)

    def test_cache_hit_skips_embedder_for_cached_chunks(self) -> None:
        cache = DictCache()
        embedder = KeywordEmbedder()
        chunks = ["match a", "noise b", "noise c", "match d", "noise e"]
        # Pre-seed the cache for every chunk.
        for c in chunks:
            cache.store[chunk_cache_key(c)] = [1.0, 0.0] if "match" in c else [0.0, 1.0]

        router = L2SemanticRouter(embedder, cache=cache)
        router.route(chunks, query="match", top_k=2)

        # Only the query was embedded; all chunk vectors came from cache.
        assert embedder.encode_calls == [["match"]]
        assert cache.set_calls == []

    def test_partial_cache_only_embeds_misses(self) -> None:
        cache = DictCache()
        embedder = KeywordEmbedder()
        chunks = ["match a", "noise b", "noise c", "match d", "noise e"]
        # Seed only the first chunk.
        cache.store[chunk_cache_key(chunks[0])] = [1.0, 0.0]

        router = L2SemanticRouter(embedder, cache=cache)
        router.route(chunks, query="match", top_k=2)

        # encode called for the query, then for exactly the 4 misses.
        miss_batch = embedder.encode_calls[1]
        assert chunks[0] not in miss_batch
        assert len(miss_batch) == 4


class TestDegradedPath:
    def test_embedder_failure_degrades_not_raises(self) -> None:
        router = L2SemanticRouter(ExplodingEmbedder())
        chunks = ["a", "b", "c", "d", "e", "f"]
        result = router.route(chunks, query="match", top_k=2)
        assert result.degraded is True
        assert result.reason is not None
        assert "RuntimeError" in result.reason
        # Degraded result returns the untouched input — no chunks lost.
        assert result.chunks == chunks

    def test_cache_failure_degrades(self) -> None:
        class ExplodingCache:
            def get(self, key: str) -> list[float] | None:
                raise ConnectionError("redis down")

            def set(self, key: str, value: list[float], ttl_seconds: int) -> None:
                pass

        router = L2SemanticRouter(KeywordEmbedder(), cache=ExplodingCache())
        result = router.route(["a", "b", "c", "d", "e", "f"], query="match", top_k=2)
        assert result.degraded is True
        assert "ConnectionError" in (result.reason or "")


class TestLatencyBudget:
    def test_top5_over_50_chunks_under_200ms(self) -> None:
        router = L2SemanticRouter(KeywordEmbedder())
        chunks = [f"chunk {i} match" if i % 2 else f"chunk {i}" for i in range(50)]
        start = time.perf_counter()
        result = router.route(chunks, query="match", top_k=5)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        assert len(result.chunks) == 5
        assert elapsed_ms <= 200.0, f"L2 took {elapsed_ms:.1f}ms (budget 200ms)"
