"""api keys for agent-to-agent (service) authentication

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-05

Subsystem 1 (Tenant & Auth). Adds the ``api_keys`` table backing programmatic /
agent-to-agent access. Like ``tenants`` and ``tenant_users`` it is an AUTH-LAYER
table with NO row-level security: a presented key must be resolved to its owning
org BEFORE any tenant binding exists, so the lookup (by the public ``prefix``) is
necessarily cross-tenant. Management endpoints re-impose isolation by filtering
on the ``org_id`` derived from the caller's RequestContext.

Only a SHA-256 hash of the secret is stored; the plaintext is shown once at
issuance and never persisted (05_security_architecture.md — secrets are never at
rest in clear). The non-superuser ``skylize_app`` runtime role is granted the DML
it needs; default privileges from 0003 also cover it, but the explicit grant
keeps this migration self-describing.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE api_keys (
            key_id            UUID PRIMARY KEY,
            org_id            TEXT NOT NULL REFERENCES tenants(org_id),
            prefix            TEXT NOT NULL UNIQUE,
            key_hash          TEXT NOT NULL,
            name              TEXT NOT NULL,
            scopes            TEXT[] NOT NULL DEFAULT '{}',
            created_by        TEXT NOT NULL,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at        TIMESTAMPTZ,
            last_used_at      TIMESTAMPTZ,
            revoked_at        TIMESTAMPTZ,
            revocation_reason TEXT
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX idx_api_keys_org ON api_keys (org_id)"
    ))
    # Auth-path lookup is by prefix and only ever wants live keys.
    op.execute(sa.text(
        "CREATE INDEX idx_api_keys_active ON api_keys (prefix) WHERE revoked_at IS NULL"
    ))

    # No RLS (see module docstring): the auth-time lookup is cross-tenant by
    # necessity. Grant the runtime app role the DML it needs to resolve and
    # manage keys; isolation for management is enforced by explicit org_id filters.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON api_keys TO {_APP_ROLE};")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON api_keys FROM {_APP_ROLE};")
    op.execute("DROP TABLE IF EXISTS api_keys CASCADE;")
