"""DeliverableService — create, retrieve, approve, revise, archive agent outputs."""

from __future__ import annotations

import structlog
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from ...dal.ports import DeliverableRepository, DeliverableRow

if TYPE_CHECKING:
    from ...memory.knowledge_ingestion import KnowledgeIngestionService

log = structlog.get_logger(__name__)


class DeliverableNotFound(Exception):
    def __init__(self, id: UUID) -> None:
        super().__init__(f"deliverable {id} not found")
        self.id = id


class DeliverableError(Exception):
    pass


_VALID_TYPES = frozenset({
    "marketing_copy", "seo_report", "ad_creative", "strategy_doc",
    "social_post", "email_copy", "landing_page", "blog_post",
    "research_report", "competitor_analysis", "other",
})

_VALID_STATUSES = frozenset({"draft", "review", "approved", "revised", "archived"})


class DeliverableService:
    def __init__(
        self,
        repo: DeliverableRepository,
        knowledge_ingestion: KnowledgeIngestionService | None = None,
    ) -> None:
        self._repo = repo
        self._knowledge_ingestion = knowledge_ingestion

    async def create_deliverable(
        self,
        *,
        org_id: str,
        agent_id: str,
        deliverable_type: str,
        title: str,
        content_markdown: str,
        metadata: dict[str, Any] | None = None,
        governance_token_id: UUID | None = None,
        summary: str | None = None,
    ) -> DeliverableRow:
        if deliverable_type not in _VALID_TYPES:
            raise DeliverableError(f"invalid deliverable_type: {deliverable_type!r}")
        now = datetime.now(timezone.utc)
        row = DeliverableRow(
            id=uuid4(),
            org_id=org_id,
            agent_id=agent_id,
            governance_token_id=governance_token_id,
            deliverable_type=deliverable_type,
            title=title,
            content_markdown=content_markdown,
            summary=summary,
            status="draft",
            version=1,
            parent_id=None,
            metadata_json=metadata or {},
            created_at=now,
            updated_at=now,
            approved_at=None,
            approved_by=None,
        )
        await self._repo.create(row)
        return row

    async def get_deliverable(self, org_id: str, deliverable_id: UUID) -> DeliverableRow:
        row = await self._repo.get_by_id(deliverable_id, org_id)
        if row is None:
            raise DeliverableNotFound(deliverable_id)
        return row

    async def list_deliverables(
        self,
        org_id: str,
        *,
        status: str | None = None,
        deliverable_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DeliverableRow], int]:
        return await self._repo.list_by_org(
            org_id,
            status=status,
            deliverable_type=deliverable_type,
            limit=limit,
            offset=offset,
        )

    async def approve_deliverable(
        self, org_id: str, deliverable_id: UUID, approved_by: str
    ) -> DeliverableRow:
        row = await self.get_deliverable(org_id, deliverable_id)
        if row.status in ("archived", "approved"):
            raise DeliverableError(
                f"cannot approve a deliverable with status '{row.status}'"
            )
        now = datetime.now(timezone.utc)
        await self._repo.update_approved(deliverable_id, org_id, approved_by, now)
        approved = await self.get_deliverable(org_id, deliverable_id)
        if self._knowledge_ingestion is not None:
            try:
                await self._knowledge_ingestion.ingest(
                    doc_id=f"deliverable:{approved.id}",
                    content=approved.content_markdown,
                    source_path=f"deliverables/{approved.deliverable_type}/{approved.title}",
                    org_id=org_id,
                )
            except Exception:
                log.warning(
                    "deliverable.embed_failed",
                    deliverable_id=str(approved.id),
                    exc_info=True,
                )
        else:
            log.warning(
                "deliverable.embed_skipped",
                deliverable_id=str(approved.id),
                reason="knowledge_ingestion_not_configured",
            )
        return approved

    async def revise_deliverable(
        self,
        org_id: str,
        deliverable_id: UUID,
        new_content: str,
        summary: str | None = None,
    ) -> DeliverableRow:
        old = await self.get_deliverable(org_id, deliverable_id)
        if old.status == "archived":
            raise DeliverableError("cannot revise an archived deliverable")
        now = datetime.now(timezone.utc)
        new_row = DeliverableRow(
            id=uuid4(),
            org_id=org_id,
            agent_id=old.agent_id,
            governance_token_id=old.governance_token_id,
            deliverable_type=old.deliverable_type,
            title=old.title,
            content_markdown=new_content,
            summary=summary,
            status="draft",
            version=old.version + 1,
            parent_id=old.id,
            metadata_json=old.metadata_json,
            created_at=now,
            updated_at=now,
            approved_at=None,
            approved_by=None,
        )
        await self._repo.create(new_row)
        await self._repo.update_status(old.id, org_id, "revised")
        return new_row

    async def archive_deliverable(
        self, org_id: str, deliverable_id: UUID
    ) -> DeliverableRow:
        await self.get_deliverable(org_id, deliverable_id)
        await self._repo.update_status(deliverable_id, org_id, "archived")
        return await self.get_deliverable(org_id, deliverable_id)

    async def list_versions(
        self, org_id: str, deliverable_id: UUID
    ) -> list[DeliverableRow]:
        await self.get_deliverable(org_id, deliverable_id)
        return await self._repo.list_versions(org_id, deliverable_id)
