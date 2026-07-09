"""users — human-user accounts with bcrypt password hashes

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-30

Human-user identity store for JWT-based authentication. Each user belongs to
exactly one org (org_id FK → tenants). The first user registered per org
receives the 'owner' role; subsequent users start as 'viewer'.

Refresh tokens are stored here too for server-side revocation.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "skylize_app"


def upgrade() -> None:
    # asyncpg executes only one statement per op.execute() call.

    op.execute(sa.text("""
        CREATE TABLE users (
            user_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id        TEXT NOT NULL REFERENCES tenants(org_id),
            email         TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            display_name  TEXT,
            roles         TEXT[] NOT NULL DEFAULT ARRAY['viewer'],
            is_active     BOOLEAN NOT NULL DEFAULT true,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_login_at TIMESTAMPTZ,
            CONSTRAINT users_email_unique UNIQUE (email)
        )
    """))

    op.execute(sa.text(
        "CREATE INDEX idx_users_org ON users (org_id)"
    ))

    op.execute(sa.text("""
        CREATE TABLE user_refresh_tokens (
            token_id   UUID PRIMARY KEY,
            user_id    UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))

    op.execute(sa.text(
        "CREATE INDEX idx_refresh_tokens_user ON user_refresh_tokens (user_id)"
    ))

    # Grant the app role access (no RLS — auth layer needs cross-tenant email lookup).
    op.execute(sa.text(
        f"GRANT SELECT, INSERT, UPDATE ON users TO {_APP_ROLE}"
    ))
    op.execute(sa.text(
        f"GRANT SELECT, INSERT, UPDATE ON user_refresh_tokens TO {_APP_ROLE}"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS user_refresh_tokens CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS users CASCADE"))
