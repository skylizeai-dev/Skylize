"""In-memory doubles for KnowledgeIngestionService tests.

FakeVectorStore mirrors the tenant-injective QdrantAdapter surface exactly
(upsert_points / verify_point / point_doc_hash / delete_by_filter / search) so
the org_id filter and stale-chunk purge are exercised for real. FakeEmbedding
records call counts so the dedup test can assert "re-embedded nothing".
"""

from __future__ import annotations

from typing import Any


class FakeVectorStore:
    """Faithful stand-in for QdrantAdapter's knowledge path (exact-match filters)."""

    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}

    async def upsert_points(self, points) -> None:
        for p in points:
            self.points[p.point_id] = dict(p.payload)

    async def verify_point(self, point_id: str, content_hash: str) -> bool:
        payload = self.points.get(point_id)
        return bool(payload) and payload.get("content_hash") == content_hash

    async def point_doc_hash(self, point_id: str) -> str | None:
        payload = self.points.get(point_id)
        return payload.get("doc_content_hash") if payload else None

    async def delete_by_filter(self, filters: dict[str, Any]) -> None:
        doomed = [
            key
            for key, payload in self.points.items()
            if all(payload.get(k) == v for k, v in filters.items())
        ]
        for key in doomed:
            del self.points[key]

    async def search(
        self, query_vector: list[float], top_k: int, filters: dict[str, Any]
    ) -> list[dict]:
        hits = [
            {"score": 1.0, **payload}
            for payload in self.points.values()
            if all(payload.get(k) == v for k, v in filters.items())
        ]
        return hits[:top_k]


class FakeEmbedding:
    """Deterministic embedder that records how often it was called."""

    def __init__(self) -> None:
        self.embed_calls = 0
        self.batch_calls = 0

    async def embed(self, text: str) -> list[float]:
        self.embed_calls += 1
        return [0.1, 0.2, 0.3, 0.4]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.batch_calls += 1
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]
