"""Tests for DeliverableService.approve_deliverable() → KnowledgeIngestionService.ingest() hook."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skylize.app.deliverables.service import DeliverableError, DeliverableService
from skylize.dal.memory import InMemoryDeliverableRepository
from skylize.dal.ports import DeliverableRow


def _make_row(
    org_id: str = "org_a",
    deliverable_type: str = "blog_post",
    title: str = "Test Post",
    content: str = "# Hello",
    status: str = "review",
) -> DeliverableRow:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return DeliverableRow(
        id=uuid4(),
        org_id=org_id,
        agent_id="agent-1",
        deliverable_type=deliverable_type,
        title=title,
        content_markdown=content,
        status=status,
        version=1,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture()
def repo() -> InMemoryDeliverableRepository:
    return InMemoryDeliverableRepository()


@pytest.fixture()
def mock_ingest() -> MagicMock:
    svc = MagicMock()
    svc.ingest = AsyncMock()
    return svc


# ---------------------------------------------------------------------------
# Embed called on approval
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approve_triggers_ingest(repo: InMemoryDeliverableRepository, mock_ingest: MagicMock) -> None:
    row = _make_row()
    await repo.create(row)
    svc = DeliverableService(repo=repo, knowledge_ingestion=mock_ingest)

    result = await svc.approve_deliverable("org_a", row.id, "reviewer@example.com")

    assert result.status == "approved"
    mock_ingest.ingest.assert_awaited_once_with(
        doc_id=f"deliverable:{row.id}",
        content="# Hello",
        source_path="deliverables/blog_post/Test Post",
        org_id="org_a",
    )


@pytest.mark.asyncio
async def test_approve_embed_uses_approved_content(repo: InMemoryDeliverableRepository, mock_ingest: MagicMock) -> None:
    row = _make_row(content="# Actual content")
    await repo.create(row)
    svc = DeliverableService(repo=repo, knowledge_ingestion=mock_ingest)
    await svc.approve_deliverable("org_a", row.id, "u1")

    _, kwargs = mock_ingest.ingest.call_args
    assert kwargs["content"] == "# Actual content"


@pytest.mark.asyncio
async def test_approve_embed_source_path_uses_type_and_title(
    repo: InMemoryDeliverableRepository, mock_ingest: MagicMock
) -> None:
    row = _make_row(deliverable_type="seo_report", title="My SEO Analysis")
    await repo.create(row)
    svc = DeliverableService(repo=repo, knowledge_ingestion=mock_ingest)
    await svc.approve_deliverable("org_a", row.id, "u1")

    _, kwargs = mock_ingest.ingest.call_args
    assert kwargs["source_path"] == "deliverables/seo_report/My SEO Analysis"


# ---------------------------------------------------------------------------
# Embed skipped / fails gracefully
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approve_no_knowledge_ingestion_still_succeeds(
    repo: InMemoryDeliverableRepository,
) -> None:
    row = _make_row()
    await repo.create(row)
    svc = DeliverableService(repo=repo, knowledge_ingestion=None)

    result = await svc.approve_deliverable("org_a", row.id, "u1")
    assert result.status == "approved"


@pytest.mark.asyncio
async def test_approve_embed_exception_does_not_block_approval(
    repo: InMemoryDeliverableRepository,
) -> None:
    row = _make_row()
    await repo.create(row)

    failing_ingest = MagicMock()
    failing_ingest.ingest = AsyncMock(side_effect=RuntimeError("Qdrant offline"))
    svc = DeliverableService(repo=repo, knowledge_ingestion=failing_ingest)

    result = await svc.approve_deliverable("org_a", row.id, "u1")
    assert result.status == "approved"
    failing_ingest.ingest.assert_awaited_once()


# ---------------------------------------------------------------------------
# Only "approved" status triggers embed (not draft/review transitions)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approve_from_draft_triggers_ingest(
    repo: InMemoryDeliverableRepository, mock_ingest: MagicMock
) -> None:
    row = _make_row(status="draft")
    await repo.create(row)
    svc = DeliverableService(repo=repo, knowledge_ingestion=mock_ingest)
    result = await svc.approve_deliverable("org_a", row.id, "u1")
    assert result.status == "approved"
    mock_ingest.ingest.assert_awaited_once()


@pytest.mark.asyncio
async def test_already_approved_raises_error(
    repo: InMemoryDeliverableRepository, mock_ingest: MagicMock
) -> None:
    row = _make_row(status="approved")
    await repo.create(row)
    svc = DeliverableService(repo=repo, knowledge_ingestion=mock_ingest)
    with pytest.raises(DeliverableError):
        await svc.approve_deliverable("org_a", row.id, "u1")
    mock_ingest.ingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_archived_raises_error_no_embed(
    repo: InMemoryDeliverableRepository, mock_ingest: MagicMock
) -> None:
    row = _make_row(status="archived")
    await repo.create(row)
    svc = DeliverableService(repo=repo, knowledge_ingestion=mock_ingest)
    with pytest.raises(DeliverableError):
        await svc.approve_deliverable("org_a", row.id, "u1")
    mock_ingest.ingest.assert_not_awaited()
