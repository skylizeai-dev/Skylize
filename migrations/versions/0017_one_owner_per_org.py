"""users — at most one 'owner' per org (race-safe registration)

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-30

`POST /api/v1/auth/register` is unauthenticated and is the only path that mints
an org owner. Before this migration it decided the new user's role by READING
the org's user list and then WRITING (`app/auth/user_service.py`) — a
read-then-write with no lock, so two simultaneous registrations for the same new
org could both observe an empty org and both be written as `owner`.

The application now also refuses registration into an org that already has any
user, via a conditional INSERT ... WHERE NOT EXISTS. That statement alone is NOT
sufficient: under READ COMMITTED the NOT EXISTS subquery takes no lock on rows
that do not exist yet, so two concurrent inserts for the same new org can both
pass it. This partial unique index is what actually settles the race — the second
writer fails with a unique violation, which the repository turns into a refusal.

Scoped to the owner role on purpose. A plain UNIQUE (org_id) would cap every org
at one user forever and foreclose an invite flow at the schema level; this
constrains only the role that registration mints.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "users_one_owner_per_org"


def upgrade() -> None:
    # A pre-existing second owner in any org would make this index creation fail
    # loudly rather than silently drop a row, which is the correct outcome: it
    # means that org needs a human decision about which account is the owner.
    op.execute(
        sa.text(
            f"CREATE UNIQUE INDEX {_INDEX} ON users (org_id) "
            "WHERE 'owner' = ANY (roles)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {_INDEX}"))
