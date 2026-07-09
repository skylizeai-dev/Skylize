"""multi-tenant credential vault — org_credentials table

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-30

Per-tenant encrypted credential store. Each row holds one provider credential
(e.g. a HubSpot API key or Slack OAuth token) encrypted by the application's
master Fernet key (SKYLIZE_CREDENTIAL_ENCRYPTION_KEY). The plaintext never
touches the database; only the Fernet ciphertext is persisted.

RLS is ENABLED + FORCED (same as the other tenant tables from migration 0001),
so the runtime app role reads and writes only within the bound org_id.

``label`` is NOT NULL DEFAULT '' — an empty string means "the default
credential for this provider". This keeps the UNIQUE constraint simple
(NULL uniqueness semantics in Postgres would require an expression index).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE org_credentials (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id          TEXT NOT NULL REFERENCES tenants(org_id),
            provider        TEXT NOT NULL,
            label           TEXT NOT NULL DEFAULT '',
            encrypted_value TEXT NOT NULL,
            metadata_json   JSONB NOT NULL DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            rotated_at      TIMESTAMPTZ
        )
    """))

    op.execute(sa.text(
        "CREATE INDEX idx_org_credentials_org_provider "
        "ON org_credentials (org_id, provider)"
    ))

    op.execute(sa.text(
        "CREATE UNIQUE INDEX idx_org_credentials_unique "
        "ON org_credentials (org_id, provider, label)"
    ))

    op.execute(sa.text("ALTER TABLE org_credentials ENABLE ROW LEVEL SECURITY"))

    op.execute(sa.text("ALTER TABLE org_credentials FORCE ROW LEVEL SECURITY"))

    op.execute(sa.text("""
        CREATE POLICY tenant_isolation ON org_credentials
        FOR ALL
        USING (org_id = current_setting('skylize.org_id', true))
        WITH CHECK (org_id = current_setting('skylize.org_id', true))
    """))

    op.execute(sa.text(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON org_credentials TO {_APP_ROLE}"
    ))


def downgrade() -> None:
    op.execute(sa.text(f"REVOKE ALL ON org_credentials FROM {_APP_ROLE}"))
    op.execute(sa.text("DROP TABLE IF EXISTS org_credentials CASCADE"))
