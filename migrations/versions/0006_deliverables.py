"""deliverables — agent output persistence layer

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-30

Subsystem 4 (Deliverables). Stores finalized agent outputs (marketing copy,
SEO reports, ad creatives, etc.) as versioned markdown documents. Tenant-scoped
with RLS. Enables dashboard display, customer download, and approval-gated
feedback into Qdrant (Terminal 3, not here).

Versioning is a linked-list via parent_id: revisions get a new row with
version + 1 and parent_id pointing to their predecessor. The root of any chain
has parent_id IS NULL.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"


def upgrade() -> None:
    # asyncpg executes only one statement per op.execute() call; every DDL
    # statement must be its own call.

    op.execute(sa.text("""
        CREATE TABLE deliverables (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id              TEXT NOT NULL REFERENCES tenants(org_id),
            agent_id            TEXT NOT NULL,
            governance_token_id UUID,
            deliverable_type    TEXT NOT NULL
                                CHECK (deliverable_type IN (
                                    'marketing_copy','seo_report','ad_creative',
                                    'strategy_doc','social_post','email_copy',
                                    'landing_page','blog_post','research_report',
                                    'competitor_analysis','other'
                                )),
            title               TEXT NOT NULL,
            content_markdown    TEXT NOT NULL,
            summary             TEXT,
            status              TEXT NOT NULL DEFAULT 'draft'
                                CHECK (status IN ('draft','review','approved','revised','archived')),
            version             INT NOT NULL DEFAULT 1,
            parent_id           UUID REFERENCES deliverables(id),
            metadata_json       JSONB NOT NULL DEFAULT '{}',
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            approved_at         TIMESTAMPTZ,
            approved_by         TEXT
        )
    """))

    op.execute(sa.text(
        "CREATE INDEX idx_deliverables_org_status ON deliverables (org_id, status, created_at DESC)"
    ))
    op.execute(sa.text(
        "CREATE INDEX idx_deliverables_org_type ON deliverables (org_id, deliverable_type)"
    ))
    op.execute(sa.text(
        "CREATE INDEX idx_deliverables_parent ON deliverables (parent_id)"
    ))

    op.execute(sa.text("ALTER TABLE deliverables ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE deliverables FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
        CREATE POLICY tenant_isolation ON deliverables
        FOR ALL
        USING (org_id = current_setting('skylize.org_id', true))
        WITH CHECK (org_id = current_setting('skylize.org_id', true))
    """))

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON deliverables TO {_APP_ROLE};")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON deliverables FROM {_APP_ROLE};")
    op.execute("DROP TABLE IF EXISTS deliverables CASCADE;")
