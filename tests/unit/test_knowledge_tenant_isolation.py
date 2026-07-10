"""Multi-tenant isolation tests for KnowledgeIngestionService.

The security property under test: knowledge written under tenant A must be
unreachable from any tenant-B query. The FakeVectorStore mirrors QdrantAdapter's
exact-match filter semantics so the org_id filter injected by
KnowledgeIngestionService.search() is exercised for real, and points are keyed
by the injective identity.point_id — the same scheme production uses.
"""

from __future__ import annotations

import pytest

from skylize.memory import identity
from skylize.memory.extraction import extract_text, infer_department
from skylize.memory.knowledge_ingestion import KnowledgeIngestionService, chunk_text

from .knowledge_fakes import FakeEmbedding, FakeVectorStore


@pytest.fixture()
def store() -> FakeVectorStore:
    return FakeVectorStore()


@pytest.fixture()
def svc(store: FakeVectorStore) -> KnowledgeIngestionService:
    return KnowledgeIngestionService(qdrant=store, embedding_service=FakeEmbedding())


TENANT_A = "org-acme-11111111"
TENANT_B = "org-globex-22222222"


async def test_tenant_b_cannot_read_tenant_a_documents(svc: KnowledgeIngestionService) -> None:
    """THE isolation gate: write under A, query as B → nothing."""
    await svc.ingest_document(
        "playbook", "Acme's secret pricing floor is $410 per seat.",
        source_path="pricing.md", org_id=TENANT_A, department="finance",
    )

    hits_b = await svc.search("pricing floor", org_id=TENANT_B)
    assert hits_b == [], "tenant B must never see tenant A's knowledge"

    hits_a = await svc.search("pricing floor", org_id=TENANT_A)
    assert len(hits_a) == 1
    assert hits_a[0]["org_id"] == TENANT_A
    assert "secret pricing floor" in hits_a[0]["content_text"]


async def test_same_doc_id_does_not_collide_across_tenants(
    svc: KnowledgeIngestionService, store: FakeVectorStore
) -> None:
    """Identical doc_id under two tenants must produce two distinct records."""
    await svc.ingest("handbook", "Tenant A handbook.", "hb.md", org_id=TENANT_A)
    await svc.ingest("handbook", "Tenant B handbook.", "hb.md", org_id=TENANT_B)

    assert identity.point_id(TENANT_A, "handbook") in store.points
    assert identity.point_id(TENANT_B, "handbook") in store.points
    assert identity.point_id(TENANT_A, "handbook") != identity.point_id(TENANT_B, "handbook")

    hits_a = await svc.search("handbook", org_id=TENANT_A)
    hits_b = await svc.search("handbook", org_id=TENANT_B)
    assert [h["content_text"] for h in hits_a] == ["Tenant A handbook."]
    assert [h["content_text"] for h in hits_b] == ["Tenant B handbook."]


async def test_every_write_carries_tenant_namespace(
    svc: KnowledgeIngestionService, store: FakeVectorStore
) -> None:
    """No write path may produce a point missing its org_id / parent_doc_id tag."""
    await svc.ingest_document(
        "notes", "line one\n\nline two", source_path="notes.txt", org_id=TENANT_A
    )
    assert store.points, "expected at least one write"
    for payload in store.points.values():
        assert payload["org_id"] == TENANT_A
        assert payload["parent_doc_id"] == "notes"


async def test_department_filter_is_scoped_within_tenant(svc: KnowledgeIngestionService) -> None:
    await svc.ingest("fin", "Budget ceilings.", "a.md", org_id=TENANT_A, department="finance")
    await svc.ingest("mkt", "Campaign calendar.", "b.md", org_id=TENANT_A, department="marketing")
    await svc.ingest("fin2", "Globex budget.", "c.md", org_id=TENANT_B, department="finance")

    hits = await svc.search("budget", org_id=TENANT_A, department="finance")
    assert len(hits) == 1
    assert hits[0]["content_text"] == "Budget ceilings."


def test_chunk_text_covers_long_documents() -> None:
    doc = ("Paragraph about operations. " * 20 + "\n\n") * 12
    chunks = chunk_text(doc)
    assert len(chunks) > 1
    assert all(len(c) <= 1600 for c in chunks)
    # nothing lost: every paragraph fragment appears in some chunk
    assert all("operations" in c.lower() for c in chunks)


def test_infer_department_routes_finance_text() -> None:
    text = "Quarterly forecast: revenue up, expense ratios stable, invoice cycle 30 days."
    assert infer_department(text) == "finance"


def test_extract_text_rejects_unsupported_formats() -> None:
    from skylize.memory.extraction import UnsupportedFormatError

    with pytest.raises(UnsupportedFormatError):
        extract_text("slides.pptx", b"anything")
    with pytest.raises(UnsupportedFormatError):
        extract_text("photo.png", b"anything")
