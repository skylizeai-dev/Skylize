"""In-memory doubles for KnowledgeIngestionService tests.

FakeVectorStore mirrors the tenant-injective QdrantAdapter surface exactly
(upsert_points / verify_point / point_doc_hash / delete_by_filter / search) so
the org_id filter and stale-chunk purge are exercised for real. FakeEmbedding
records call counts so the dedup test can assert "re-embedded nothing".

It mirrors the adapter's ORG SCOPE CONTRACT, not just its method names: it routes
through the same `memory.org_scope` helpers the real adapter uses, so `org_id` is
required, the store (not the caller) applies the org condition, and a read by
point id fails closed on a foreign point. A lenient double here would let a call
site that the real adapter rejects pass its tests.
"""

from __future__ import annotations

from typing import Any

from skylize.memory.org_scope import ORG_FIELD, require_org, scoped_filters


class FakeVectorStore:
    """Faithful stand-in for QdrantAdapter's knowledge path (exact-match filters)."""

    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}

    async def upsert_points(self, points) -> None:
        for p in points:
            require_org(p.org_id)
            self.points[p.point_id] = {**dict(p.payload), ORG_FIELD: p.org_id}

    async def verify_point(self, point_id: str, content_hash: str, *, org_id: str) -> bool:
        payload = self._scoped_payload(point_id, org_id)
        return payload is not None and payload.get("content_hash") == content_hash

    async def point_doc_hash(self, point_id: str, *, org_id: str) -> str | None:
        payload = self._scoped_payload(point_id, org_id)
        return payload.get("doc_content_hash") if payload else None

    async def delete_by_filter(
        self, filters: dict[str, Any] | None = None, *, org_id: str
    ) -> None:
        effective = scoped_filters(org_id, filters)
        doomed = [
            key
            for key, payload in self.points.items()
            if all(payload.get(k) == v for k, v in effective.items())
        ]
        for key in doomed:
            del self.points[key]

    async def search(
        self,
        query_vector: list[float],
        top_k: int,
        filters: dict[str, Any] | None = None,
        *,
        org_id: str,
    ) -> list[dict]:
        effective = scoped_filters(org_id, filters)
        hits = [
            {"score": 1.0, **payload}
            for payload in self.points.values()
            if all(payload.get(k) == v for k, v in effective.items())
        ]
        return hits[:top_k]

    # ── internals ───────────────────────────────────────────────────────────
    def _scoped_payload(self, point_id: str, org_id: str) -> dict[str, Any] | None:
        require_org(org_id)
        payload = self.points.get(point_id)
        if not payload or payload.get(ORG_FIELD) != org_id:
            return None
        return payload


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
