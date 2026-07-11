"""DeliverableService — business logic over the `DeliverableRepository` port.

The service owns identity, versioning, and status transitions; the repository
owns SQL and tenant scoping. Every method is `org_id`-scoped (tenant isolation
at IF-DATA). Agents persist their output through `create_deliverable`; humans
approve/revise/archive through the same service the API routes call.

Status lifecycle: ``draft`` → ``review`` → ``approved`` (terminal-happy);
``revised`` marks a superseded version whose successor carries ``parent_id``;
``archived`` is soft-deletion.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog

from ...dal.ports import DeliverableRepository, DeliverableRow

if TYPE_CHECKING:
    from ...memory.knowledge_ingestion import KnowledgeIngestionService

log = structlog.get_logger(__name__)


class DeliverableError(Exception):
    """Base error for invalid deliverable operations (maps to 4xx at the edge)."""


class DeliverableNotFound(DeliverableError):
    """The requested deliverable does not exist within this tenant."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DeliverableService:
    def __init__(
        self,
        repo: DeliverableRepository,
        knowledge_ingestion: KnowledgeIngestionService | None = None,
    ) -> None:
        self._repo = repo
        # Closed loop: approved deliverables are embedded back into the tenant's
        # knowledge memory. Optional — approval must never fail on a missing or
        # broken vector backend.
        self._knowledge_ingestion = knowledge_ingestion

    async def create_deliverable(
        self,
        *,
        org_id: str,
        agent_id: str,
        deliverable_type: str,
        title: str,
        content_markdown: str,
        governance_token_id: UUID | None = None,
        summary: str = "",
        metadata: dict[str, object] | None = None,
    ) -> DeliverableRow:
        if not title.strip():
            raise DeliverableError("title must not be empty")
        if not content_markdown.strip():
            raise DeliverableError("content_markdown must not be empty")
        now = _now()
        row = DeliverableRow(
            id=uuid4(),
            org_id=org_id,
            agent_id=agent_id,
            deliverable_type=deliverable_type,
            title=title,
            content_markdown=content_markdown,
            summary=summary,
            status="draft",
            version=1,
            created_at=now,
            updated_at=now,
            governance_token_id=governance_token_id,
            parent_id=None,
            metadata_json=dict(metadata or {}),
        )
        await self._repo.create(row)
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

    async def get_deliverable(self, org_id: str, id: UUID) -> DeliverableRow:
        row = await self._repo.get_by_id(id, org_id)
        if row is None:
            raise DeliverableNotFound(str(id))
        return row

    async def approve_deliverable(
        self, org_id: str, id: UUID, approved_by: str
    ) -> DeliverableRow:
        current = await self.get_deliverable(org_id, id)
        if current.status in ("archived", "approved"):
            raise DeliverableError(
                f"cannot approve a deliverable with status '{current.status}'"
            )
        await self._repo.update_approved(id, org_id, approved_by, _now())
        approved = await self.get_deliverable(org_id, id)
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
        self, org_id: str, id: UUID, content_markdown: str, summary: str
    ) -> DeliverableRow:
        current = await self.get_deliverable(org_id, id)
        if current.status == "archived":
            raise DeliverableError("cannot revise an archived deliverable")
        if not content_markdown.strip():
            raise DeliverableError("content_markdown must not be empty")
        now = _now()
        new_row = DeliverableRow(
            id=uuid4(),
            org_id=org_id,
            agent_id=current.agent_id,
            deliverable_type=current.deliverable_type,
            title=current.title,
            content_markdown=content_markdown,
            summary=summary,
            status="draft",
            version=current.version + 1,
            created_at=now,
            updated_at=now,
            governance_token_id=current.governance_token_id,
            parent_id=current.id,
            metadata_json=dict(current.metadata_json),
        )
        await self._repo.create(new_row)
        await self._repo.update_status(current.id, org_id, "revised")
        return new_row

    async def archive_deliverable(self, org_id: str, id: UUID) -> DeliverableRow:
        await self.get_deliverable(org_id, id)
        await self._repo.update_status(id, org_id, "archived")
        return await self.get_deliverable(org_id, id)

    async def list_versions(self, org_id: str, id: UUID) -> list[DeliverableRow]:
        return await self._repo.list_versions(org_id, id)
