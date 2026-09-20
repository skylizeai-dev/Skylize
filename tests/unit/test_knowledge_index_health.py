"""Index health is a CENSUS, not an estimate.

Every figure this endpoint reports has to be a count or a max over points the
tenant really holds. These tests ingest through the real service into the
org-scope-faithful FakeVectorStore and then assert the aggregate against what
was actually written — so a regression that starts sampling, guessing, or
leaking across tenants fails here.

The negative tests matter as much as the positive ones: the console mock for
this screen showed connector types, sync statuses and coverage percentages, and
none of those has a backend fact behind it. `test_index_health_invents_no_
source_metadata` pins that the response model cannot grow one quietly.
"""

from __future__ import annotations

import pytest

from skylize.memory.knowledge_ingestion import (
    CHUNK_SIZE,
    IndexHealth,
    KnowledgeIngestionService,
)

from .knowledge_fakes import FakeEmbedding, FakeVectorStore

TENANT_A = "org-acme-11111111"
TENANT_B = "org-globex-22222222"


@pytest.fixture()
def store() -> FakeVectorStore:
    return FakeVectorStore()


@pytest.fixture()
def svc(store: FakeVectorStore) -> KnowledgeIngestionService:
    return KnowledgeIngestionService(qdrant=store, embedding_service=FakeEmbedding())


async def test_empty_index_reports_zeroes_not_an_error(
    svc: KnowledgeIngestionService,
) -> None:
    """A tenant that has ingested nothing is a valid, empty census."""
    health = await svc.index_health(org_id=TENANT_A)
    assert health == IndexHealth(
        total_chunks=0,
        total_documents=0,
        source_paths=[],
        last_ingested_at=None,
        truncated=False,
    )


async def test_counts_match_what_was_actually_ingested(
    svc: KnowledgeIngestionService, store: FakeVectorStore
) -> None:
    """Chunk and document totals are the real point/parent counts, not a guess."""
    # Long enough to chunk into several points under one parent_doc_id.
    long_doc = "Refund policy paragraph.\n\n" * (CHUNK_SIZE // 10)
    written = await svc.ingest_document(
        "handbook", long_doc, source_path="handbook.md",
        org_id=TENANT_A, department="support",
    )
    assert written > 1, "fixture must actually chunk for this test to mean anything"
    await svc.ingest_document(
        "policy", "A short policy note.", source_path="policy.md",
        org_id=TENANT_A, department="legal",
    )

    health = await svc.index_health(org_id=TENANT_A)

    assert health.total_chunks == len(store.points)
    assert health.total_documents == 2
    assert health.truncated is False

    by_path = {s.source_path: s for s in health.source_paths}
    assert set(by_path) == {"handbook.md", "policy.md"}
    assert by_path["handbook.md"].chunks == written
    assert by_path["handbook.md"].documents == 1
    assert by_path["handbook.md"].departments == ["support"]
    assert by_path["policy.md"].chunks == 1
    assert by_path["policy.md"].departments == ["legal"]


async def test_source_paths_are_ordered_by_size_then_name(
    svc: KnowledgeIngestionService,
) -> None:
    """Deterministic ordering: the console must not see rows shuffle per request."""
    big = "Escalation path detail.\n\n" * (CHUNK_SIZE // 10)
    await svc.ingest_document("big", big, source_path="big.md", org_id=TENANT_A)
    await svc.ingest_document("a", "short", source_path="a.md", org_id=TENANT_A)
    await svc.ingest_document("b", "short too", source_path="b.md", org_id=TENANT_A)

    paths = [s.source_path for s in (await svc.index_health(org_id=TENANT_A)).source_paths]
    assert paths[0] == "big.md"  # most chunks first
    assert paths[1:] == ["a.md", "b.md"]  # ties broken by name, not by chance


async def test_multiple_documents_under_one_source_path_are_distinguished(
    svc: KnowledgeIngestionService,
) -> None:
    """source_path is not a key: two docs can share one origin string.

    `interview_knowledge` ingests every answer under the literal
    "onboarding-interview", so conflating documents with source paths would
    report an entire interview as a single document.
    """
    for i, answer in enumerate(["We refund within 30 days.", "We ship on Tuesdays."]):
        await svc.ingest_document(
            f"interview-{i}", answer,
            source_path="onboarding-interview", org_id=TENANT_A,
        )

    health = await svc.index_health(org_id=TENANT_A)
    assert len(health.source_paths) == 1
    row = health.source_paths[0]
    assert row.source_path == "onboarding-interview"
    assert row.documents == 2
    assert row.chunks == 2
    assert health.total_documents == 2


async def test_index_health_cannot_see_another_tenants_points(
    svc: KnowledgeIngestionService,
) -> None:
    """THE isolation gate for this read: A's census must not count B's points."""
    await svc.ingest_document(
        "playbook", "Acme's secret pricing floor is $410 per seat.",
        source_path="acme-pricing.md", org_id=TENANT_A,
    )

    health_b = await svc.index_health(org_id=TENANT_B)
    assert health_b.total_chunks == 0
    assert health_b.total_documents == 0
    assert health_b.source_paths == []
    # Not just the counts: the other tenant's origin string is itself a leak.
    assert "acme-pricing.md" not in [s.source_path for s in health_b.source_paths]

    health_a = await svc.index_health(org_id=TENANT_A)
    assert health_a.total_documents == 1


async def test_blank_org_scope_fails_closed(svc: KnowledgeIngestionService) -> None:
    """An unscoped census would be a read of every tenant's index."""
    from skylize.memory.org_scope import OrgScopeRequired

    with pytest.raises(OrgScopeRequired):
        await svc.index_health(org_id="")


async def test_freshness_is_the_latest_ingest_not_a_source_change(
    svc: KnowledgeIngestionService,
) -> None:
    """last_ingested_at is a real MAX over stored ingested_at values."""
    await svc.ingest_document("d1", "first", source_path="one.md", org_id=TENANT_A)
    health = await svc.index_health(org_id=TENANT_A)

    stamps = [p["ingested_at"] for p in (await _payloads(svc))]
    assert health.last_ingested_at == max(stamps)
    assert health.source_paths[0].last_ingested_at == max(stamps)


async def test_index_health_invents_no_source_metadata() -> None:
    """The honesty gate, pinned in the schema itself.

    The console mock for this screen showed a connector type (DATABASE /
    CONNECTOR / STREAM / WAREHOUSE), a SYNCED/INDEXING status, a coverage
    percentage and a recall p95. The backend stores no fact behind any of them,
    so none may appear on this model — and the model forbids extras, so a
    caller cannot smuggle one in either.
    """
    from skylize.memory.knowledge_ingestion import SourcePathStats

    fields = set(SourcePathStats.model_fields) | set(IndexHealth.model_fields)
    for invented in (
        "connector_type", "source_type", "status", "sync_status",
        "coverage", "coverage_pct", "recall_p95", "latency_p95",
        "next_sync_at", "last_synced_at", "health_score",
    ):
        assert invented not in fields, f"{invented} has no backend source and must not exist"

    with pytest.raises(Exception):
        SourcePathStats(
            source_path="x", chunks=1, documents=1,
            departments=[], last_ingested_at=None,
            coverage=0.97,  # type: ignore[call-arg]
        )


async def test_truncated_walk_is_reported_not_hidden(
    store: FakeVectorStore, svc: KnowledgeIngestionService
) -> None:
    """Past the adapter's cap the counts are a LOWER BOUND and must say so."""
    await svc.ingest_document("d", "content", source_path="one.md", org_id=TENANT_A)

    real_scroll = store.scroll_payloads

    async def capped(fields, *, org_id, page_size=256, max_points=10_000):
        return await real_scroll(fields, org_id=org_id, page_size=page_size, max_points=0)

    store.scroll_payloads = capped  # type: ignore[assignment]
    health = await svc.index_health(org_id=TENANT_A)
    assert health.truncated is True
    assert health.total_chunks == 0  # a prefix of zero points, honestly labelled


async def _payloads(svc: KnowledgeIngestionService) -> list[dict]:
    store = svc._qdrant  # type: ignore[attr-defined]
    payloads, _ = await store.scroll_payloads(["ingested_at"], org_id=TENANT_A)
    return payloads
