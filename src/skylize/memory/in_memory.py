"""InMemoryVectorStore — dict-backed VectorStore for backend="memory" and tests.

Satisfies the same duck-type interface as QdrantAdapter (upsert_vector / search).
No ML embeddings — uses cosine similarity over whatever float list is provided.

It mirrors QdrantAdapter's org-scope contract as well as its shape: ``org_id`` is
a required keyword-only argument, the store (not the caller) applies the org
condition, and ``org_id`` inside ``filters`` is rejected. A test double that let
an unscoped read through would make the structural guarantee untestable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .org_scope import ORG_FIELD, require_org, scoped_filters


@dataclass
class _VectorDoc:
    doc_id: str
    vector: list[float]
    metadata: dict[str, Any]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


class InMemoryVectorStore:
    """Ephemeral in-process vector store for tests and the memory backend.

    API mirrors QdrantAdapter so bootstrap.py can swap it in without changing
    MemoryService internals.
    """

    def __init__(self) -> None:
        self._docs: dict[str, _VectorDoc] = {}

    async def upsert_vector(
        self, doc_id: str, vector: list[float], metadata: dict[str, Any], *, org_id: str
    ) -> None:
        require_org(org_id)
        self._docs[doc_id] = _VectorDoc(
            doc_id=doc_id,
            vector=vector,
            # org stamped LAST, as QdrantAdapter does.
            metadata={**metadata, ORG_FIELD: org_id},
        )

    async def search(
        self,
        query_vector: list[float],
        top_k: int,
        filters: dict[str, Any] | None = None,
        *,
        org_id: str,
    ) -> list[dict[str, Any]]:
        effective = scoped_filters(org_id, filters)
        results: list[tuple[float, _VectorDoc]] = []
        for doc in self._docs.values():
            # Apply metadata filters (org condition always among them)
            if not all(doc.metadata.get(k) == v for k, v in effective.items()):
                continue
            score = _cosine(query_vector, doc.vector)
            results.append((score, doc))
        results.sort(key=lambda t: t[0], reverse=True)
        return [
            {"score": score, **doc.metadata}
            for score, doc in results[:top_k]
        ]

    async def verify_document(self, doc_id: str, content_hash: str, *, org_id: str) -> bool:
        require_org(org_id)
        doc = self._docs.get(doc_id)
        if doc is None or doc.metadata.get(ORG_FIELD) != org_id:
            return False
        return doc.metadata.get("content_hash") == content_hash

    def clear(self) -> None:
        self._docs.clear()

    @property
    def count(self) -> int:
        return len(self._docs)
