"""Unit tests: MemoryService recall, commit, org_id isolation, and fallback."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest

from skylize.events.memory_bus import InMemoryEventBus
from skylize.memory.in_memory import InMemoryVectorStore
from skylize.memory.service import MemoryService
from skylize.schemas.memory import MemoryEntry, MemoryScope


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _InMemoryMemoryRepository:
    """Minimal in-memory MemoryRepository (dal.ports.MemoryRepository protocol)."""

    def __init__(self) -> None:
        self._entries: list[MemoryEntry] = []
        self._superseded: dict[UUID, UUID] = {}

    async def write(self, entry: MemoryEntry) -> None:
        self._entries.append(entry)

    async def search(
        self, scope: MemoryScope, query_embedding: list[float], limit: int = 5
    ) -> list[MemoryEntry]:
        return [
            e for e in self._entries
            if e.org_id == scope.org_id
            and (scope.department is None or e.department == scope.department)
            and e.superseded_by is None
        ][:limit]

    async def get_by_session(self, scope: MemoryScope) -> list[MemoryEntry]:
        return [e for e in self._entries if e.org_id == scope.org_id]

    async def supersede(self, entry_id: UUID, superseded_by: UUID) -> None:
        self._superseded[entry_id] = superseded_by
        for i, e in enumerate(self._entries):
            if e.entry_id == entry_id:
                self._entries[i] = e.model_copy(update={"superseded_by": superseded_by})
                break


def _fake_embed(text: str) -> list[float]:
    # Deterministic: each char's ordinal as a dimension (truncated to 8 dims for speed)
    vals = [float(ord(c)) for c in text[:8]]
    while len(vals) < 8:
        vals.append(0.0)
    return vals


def _make_svc(
    repo: _InMemoryMemoryRepository | None = None,
    bus: InMemoryEventBus | None = None,
    qdrant: InMemoryVectorStore | None = None,
) -> tuple[MemoryService, _InMemoryMemoryRepository, InMemoryEventBus]:
    repo = repo or _InMemoryMemoryRepository()
    bus = bus or InMemoryEventBus()
    svc = MemoryService(
        repo=repo,
        embedding_fn=_fake_embed,
        bus=bus,
        qdrant_adapter=qdrant,
    )
    return svc, repo, bus


# ---------------------------------------------------------------------------
# Tests: recall
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_returns_empty_on_no_data() -> None:
    svc, _, _ = _make_svc()
    results = await svc.recall("marketing", "org-1", "quarterly revenue")
    assert results == []


@pytest.mark.asyncio
async def test_recall_pg_fallback_when_no_qdrant() -> None:
    """Without Qdrant, must fall back to Postgres FTS."""
    repo = _InMemoryMemoryRepository()
    svc, _, _ = _make_svc(repo=repo, qdrant=None)

    # Pre-populate Postgres
    entry = MemoryEntry(
        org_id="org-1",
        agent_id="ceo",
        scope="marketing",
        department="marketing",
        tier="episodic",
        content_text="Q3 revenue exceeded target by 12%",
        created_by_agent="ceo",
        importance_score=0.9,
    )
    await repo.write(entry)

    results = await svc.recall("marketing", "org-1", "quarterly revenue")
    assert len(results) == 1
    assert results[0].content_text == "Q3 revenue exceeded target by 12%"


@pytest.mark.asyncio
async def test_recall_uses_qdrant_primary() -> None:
    qdrant = InMemoryVectorStore()
    svc, repo, _ = _make_svc(qdrant=qdrant)

    # Upsert directly into Qdrant store
    await qdrant.upsert_vector(
        doc_id="org-1:marketing:abc123",
        vector=_fake_embed("marketing KPI"),
        metadata={
            "org_id": "org-1",
            "namespace": "marketing",
            "entry_id": str(uuid4()),
            "agent_id": "ceo",
            "text": "marketing KPI dashboard",
            "content_hash": "abc",
            "importance_score": 0.85,
            "created_by_agent": "ceo",
        },
    )

    results = await svc.recall("marketing", "org-1", "marketing KPI")
    assert len(results) == 1
    assert "marketing KPI dashboard" in results[0].content_text


@pytest.mark.asyncio
async def test_recall_dedupes_results() -> None:
    """Same content from both Qdrant and Postgres fallback must dedupe."""
    repo = _InMemoryMemoryRepository()
    qdrant = InMemoryVectorStore()
    svc, _, _ = _make_svc(repo=repo, qdrant=qdrant)

    content_hash = "dedup_test_hash_001"
    entry = MemoryEntry(
        org_id="org-1",
        agent_id="ceo",
        scope="ops",
        department="ops",
        tier="episodic",
        content_text="duplicate content",
        content_hash=content_hash,
        created_by_agent="ceo",
        importance_score=0.7,
    )
    await repo.write(entry)

    # Qdrant returns nothing (different namespace), so only PG fallback fires
    results = await svc.recall("ops", "org-1", "duplicate content")
    # Should only have 1 copy
    hashes = [r.content_hash for r in results]
    assert len(hashes) == len(set(hashes))


@pytest.mark.asyncio
async def test_recall_emits_event() -> None:
    bus = InMemoryEventBus()
    svc, repo, _ = _make_svc(bus=bus)

    await repo.write(
        MemoryEntry(
            org_id="org-1",
            agent_id="ceo",
            scope="finance",
            department="finance",
            tier="episodic",
            content_text="budget approved",
            created_by_agent="ceo",
        )
    )
    await svc.recall("finance", "org-1", "budget")
    served = bus.published_of_type("memory.recall_served")
    assert len(served) == 1
    assert served[0].payload.namespace == "finance"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Tests: commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_persists_to_postgres() -> None:
    repo = _InMemoryMemoryRepository()
    svc, _, _ = _make_svc(repo=repo)

    entry_id = await svc.commit("ops", "org-1", "server restarted at 03:00", {})
    assert isinstance(entry_id, UUID)
    assert len(repo._entries) == 1
    assert repo._entries[0].content_text == "server restarted at 03:00"
    assert repo._entries[0].org_id == "org-1"


@pytest.mark.asyncio
async def test_commit_emits_committed_event() -> None:
    bus = InMemoryEventBus()
    repo = _InMemoryMemoryRepository()
    svc, _, _ = _make_svc(repo=repo, bus=bus)

    await svc.commit("ops", "org-1", "deploy successful", {})
    events = bus.published_of_type("memory.committed")
    assert len(events) == 1
    assert events[0].payload.namespace == "ops"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_commit_indexes_to_qdrant() -> None:
    qdrant = InMemoryVectorStore()
    repo = _InMemoryMemoryRepository()
    svc, _, _ = _make_svc(repo=repo, qdrant=qdrant)

    await svc.commit("ops", "org-1", "metrics collected", {})
    # Give the background task time to run
    await asyncio.sleep(0.05)
    assert qdrant.count == 1


@pytest.mark.asyncio
async def test_commit_supersede_emits_invalidated_event() -> None:
    bus = InMemoryEventBus()
    repo = _InMemoryMemoryRepository()
    svc, _, _ = _make_svc(repo=repo, bus=bus)

    old_id = await svc.commit("ops", "org-1", "old memory", {})
    await svc.commit("ops", "org-1", "new memory replacing old", {}, supersede_entry_id=old_id)

    invalidated = bus.published_of_type("memory.invalidated")
    assert len(invalidated) == 1
    assert invalidated[0].payload.record_id == old_id  # type: ignore[attr-defined]
    assert invalidated[0].payload.superseded_by is not None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Tests: org_id isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_org_id_isolation_in_recall() -> None:
    """org-A's data must not appear in org-B's recall."""
    repo = _InMemoryMemoryRepository()
    svc, _, _ = _make_svc(repo=repo)

    await repo.write(
        MemoryEntry(
            org_id="org-A",
            agent_id="ceo",
            scope="marketing",
            department="marketing",
            tier="episodic",
            content_text="secret org-A data",
            created_by_agent="ceo",
        )
    )

    results = await svc.recall("marketing", "org-B", "secret")
    assert results == []


@pytest.mark.asyncio
async def test_org_id_isolation_commit_stores_correct_org() -> None:
    repo = _InMemoryMemoryRepository()
    svc, _, _ = _make_svc(repo=repo)

    await svc.commit("ops", "org-X", "org-X private data", {})
    assert repo._entries[0].org_id == "org-X"

    # org-Y recall sees nothing
    results = await svc.recall("ops", "org-Y", "private data")
    assert results == []


# ---------------------------------------------------------------------------
# Tests: InMemoryVectorStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_in_memory_vector_store_upsert_search() -> None:
    store = InMemoryVectorStore()
    vec = [1.0, 0.0, 0.0, 0.0]
    await store.upsert_vector("doc-1", vec, {"org_id": "org-1", "namespace": "ops", "text": "hello"})
    await store.upsert_vector("doc-2", [0.0, 1.0, 0.0, 0.0], {"org_id": "org-2", "namespace": "ops", "text": "bye"})

    results = await store.search([1.0, 0.0, 0.0, 0.0], top_k=5, filters={"org_id": "org-1"})
    assert len(results) == 1
    assert results[0]["text"] == "hello"


@pytest.mark.asyncio
async def test_in_memory_vector_store_filters_namespace() -> None:
    store = InMemoryVectorStore()
    await store.upsert_vector("a", [1.0], {"org_id": "org-1", "namespace": "marketing", "text": "a"})
    await store.upsert_vector("b", [1.0], {"org_id": "org-1", "namespace": "ops", "text": "b"})

    results = await store.search([1.0], top_k=5, filters={"org_id": "org-1", "namespace": "marketing"})
    assert len(results) == 1
    assert results[0]["text"] == "a"
