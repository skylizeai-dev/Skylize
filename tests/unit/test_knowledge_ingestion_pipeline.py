"""Pipeline behaviour of KnowledgeIngestionService: dedup, collision, stale-chunk
cleanup, and the memory.search round-trip that proves the payload-key fix.
"""

from __future__ import annotations

import pytest

from skylize.memory import identity
from skylize.memory.knowledge_ingestion import KnowledgeIngestionService
from skylize.tools.builtin.memory_recall import QdrantMemoryRecallPort

from .knowledge_fakes import FakeEmbedding, FakeVectorStore

ORG = "org_acme"


@pytest.fixture()
def store() -> FakeVectorStore:
    return FakeVectorStore()


@pytest.fixture()
def embed() -> FakeEmbedding:
    return FakeEmbedding()


@pytest.fixture()
def svc(store: FakeVectorStore, embed: FakeEmbedding) -> KnowledgeIngestionService:
    return KnowledgeIngestionService(qdrant=store, embedding_service=embed)


async def test_memory_search_returns_content_not_filename(
    svc: KnowledgeIngestionService, store: FakeVectorStore, embed: FakeEmbedding
) -> None:
    """Round-trip: ingested text is recalled as MemoryHit.content, not the path."""
    await svc.ingest_document(
        "upload/x", "The refund policy is thirty days from purchase.",
        source_path="policy.pdf", org_id=ORG,
    )
    port = QdrantMemoryRecallPort(embedding_service=embed, qdrant=store)
    hits = await port.recall(query="refund", top_k=5, org_id=ORG, namespace=None)
    assert hits, "expected a hit"
    assert hits[0].content == "The refund policy is thirty days from purchase."
    assert hits[0].content != "policy.pdf"


async def test_identical_reingest_reembeds_nothing(
    svc: KnowledgeIngestionService, embed: FakeEmbedding
) -> None:
    n1 = await svc.ingest_document("upload/x", "alpha beta gamma", source_path="a.txt", org_id=ORG)
    calls_after_first = embed.batch_calls
    n2 = await svc.ingest_document("upload/x", "alpha beta gamma", source_path="a.txt", org_id=ORG)
    assert n2 == n1
    assert embed.batch_calls == calls_after_first, "second ingest must re-embed nothing"


async def test_same_second_distinct_content_does_not_overwrite(
    svc: KnowledgeIngestionService, store: FakeVectorStore
) -> None:
    """Two interview answers submitted in the same second → two distinct points."""
    id1 = identity.content_doc_id(b"answer one", prefix="interview")
    id2 = identity.content_doc_id(b"answer two", prefix="interview")
    assert id1 != id2
    await svc.ingest_document(id1, "answer one", source_path="onboarding", org_id=ORG)
    await svc.ingest_document(id2, "answer two", source_path="onboarding", org_id=ORG)
    texts = sorted(p["content_text"] for p in store.points.values())
    assert texts == ["answer one", "answer two"]


async def test_shorter_reingest_purges_stale_chunks(
    svc: KnowledgeIngestionService, store: FakeVectorStore
) -> None:
    long_doc = ("Operational paragraph about logistics. " * 60 + "\n\n") * 8
    await svc.ingest_document("doc-x", long_doc, source_path="ops.md", org_id=ORG)
    first_chunks = [p for p in store.points.values() if p["parent_doc_id"] == "doc-x"]
    assert len(first_chunks) > 1, "long document should span multiple chunks"

    await svc.ingest_document("doc-x", "Short replacement.", source_path="ops.md", org_id=ORG)
    remaining = [p for p in store.points.values() if p["parent_doc_id"] == "doc-x"]
    assert len(remaining) == 1
    assert remaining[0]["content_text"] == "Short replacement."


async def test_tenant_b_cannot_read_tenant_a(
    svc: KnowledgeIngestionService,
) -> None:
    await svc.ingest_document(
        "playbook", "Acme's secret pricing floor is $410 per seat.",
        source_path="pricing.md", org_id="org_a", department="finance",
    )
    assert await svc.search("pricing", org_id="org_b") == []
    hits_a = await svc.search("pricing", org_id="org_a")
    assert len(hits_a) == 1 and hits_a[0]["org_id"] == "org_a"
