"""InMemoryVectorStore — dict-backed VectorStore for backend="memory" and tests.

Satisfies the same duck-type interface as QdrantAdapter (upsert_vector / search).
No ML embeddings — uses cosine similarity over whatever float list is provided.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


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
        self, doc_id: str, vector: list[float], metadata: dict[str, Any]
    ) -> None:
        self._docs[doc_id] = _VectorDoc(doc_id=doc_id, vector=vector, metadata=metadata)

    async def search(
        self,
        query_vector: list[float],
        top_k: int,
        filters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        results: list[tuple[float, _VectorDoc]] = []
        for doc in self._docs.values():
            # Apply metadata filters
            if not all(doc.metadata.get(k) == v for k, v in filters.items()):
                continue
            score = _cosine(query_vector, doc.vector)
            results.append((score, doc))
        results.sort(key=lambda t: t[0], reverse=True)
        return [
            {"score": score, **doc.metadata}
            for score, doc in results[:top_k]
        ]

    async def verify_document(self, doc_id: str, content_hash: str) -> bool:
        doc = self._docs.get(doc_id)
        if doc is None:
            return False
        return doc.metadata.get("content_hash") == content_hash

    def clear(self) -> None:
        self._docs.clear()

    @property
    def count(self) -> int:
        return len(self._docs)
