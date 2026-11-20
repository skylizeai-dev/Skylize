"""user_refresh_tokens.family_id: refresh-token lineage for per-family revocation

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-20

WHY. Reuse detection that REVOKES needs to know which tokens descend from the
same login (design doc docs/06_integrations/myday_session_revocation_design.md,
sections 7.0 and 9.1). `user_refresh_tokens` (0008_users.py) has no lineage:
nothing links a token to the one it replaced. This adds that link as a family
id: a login roots a new family at its own token id, and every rotation copies
the consumed row's family into the new row inside the same transaction
(dal/users.py `rotate_refresh_token`). A detected replay then revokes the live
rows of THAT family only (`revoke_family`), never every token of the user: the
user-wide variant proposed in PR #47 re-triggered on rows it had itself revoked
and cascaded logouts across all devices (owner ruling R-B).

NULL HANDLING, AND WHY THIS ONE. Four statements, all in this migration's one
transaction (the ADD COLUMN takes the table's ACCESS EXCLUSIVE lock, so no row
can be written between them):

  1. ADD COLUMN family_id UUID, nullable, no default: catalogue-only, no rewrite.
  2. Backfill `family_id = token_id` for every existing row. `token_id` is the
     primary key, so every pre-migration token becomes the root of its OWN
     family: none is merged into another's, and the rule is the same one a
     login applies to a new token. A backfilled token keeps refreshing: the
     consume condition (`_CONSUME_SQL`) does not look at the family, and its
     rotation copies `family_id` (= its own token id) into the new row.
  3. SET DEFAULT gen_random_uuid() (built in since Postgres 13; 0008 already
     relies on it for users.user_id). This is for the ROLLING DEPLOY: app code
     from before this migration inserts (token_id, user_id, expires_at) without
     the column, and that INSERT must not fail. It gets a fresh random family,
     i.e. a family of its own. The one consequence: a rotation done by OLD code
     starts a new family instead of continuing its parent's, which can only
     NARROW a later revocation (the old part of the lineage is revoked without
     the new part), never widen it. New code always writes the column.
  4. SET NOT NULL. After 2 and 3 no row can be NULL, so NULL is never a family
     the application has to interpret: `revoke_family(NULL)` and a NULL-family
     rotation cannot occur. Rejected alternatives: leaving the column nullable
     (every reader would need a NULL rule, and a NULL slipping into
     `WHERE family_id = $1` is a silent no-op), and a BEFORE INSERT trigger
     copying token_id (a new function object for the same guarantee 3 gives).

INDEX. `revoke_family` runs
`UPDATE ... WHERE family_id = $1 AND revoked_at IS NULL AND expires_at > now()`.
A family has at most one live row, while consumed rows accumulate (one per
rotation, nothing deletes them), so the index is PARTIAL on `revoked_at IS
NULL`: it holds only live rows and stays small. The query's predicate implies
the index predicate, so the planner can use it.

GRANTS AND RLS. Unchanged. 0008 grants SELECT, INSERT, UPDATE on the TABLE to
skylize_app; a table-level grant covers columns added later, so the app role
can read, insert and update `family_id` with no new GRANT. The table has no RLS
(0008: the auth layer needs cross-tenant lookups) and this adds none.

DEPLOY ORDER. Apply this migration BEFORE the code that writes `family_id`
serves traffic (CI's integration job runs `alembic upgrade head` first). The
reverse order fails closed: new code's INSERT names a column that does not
exist yet, so login and refresh error instead of minting family-less tokens.

DOWNGRADE drops exactly what upgrade added: the index, then the column (its
default and NOT NULL go with it). Existing rows and every other column are
untouched. Run it only after the app is back on code that does not write
`family_id`.

asyncpg executes one statement per op.execute() call, so each statement is its
own call.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE user_refresh_tokens ADD COLUMN family_id UUID"
    ))
    op.execute(sa.text(
        "UPDATE user_refresh_tokens SET family_id = token_id WHERE family_id IS NULL"
    ))
    op.execute(sa.text(
        "ALTER TABLE user_refresh_tokens ALTER COLUMN family_id SET DEFAULT gen_random_uuid()"
    ))
    op.execute(sa.text(
        "ALTER TABLE user_refresh_tokens ALTER COLUMN family_id SET NOT NULL"
    ))
    op.execute(sa.text(
        "CREATE INDEX idx_refresh_tokens_family_live ON user_refresh_tokens (family_id) "
        "WHERE revoked_at IS NULL"
    ))


def downgrade() -> None:
    # No IF EXISTS: downgrading from 0038 means upgrade() ran, so both objects
    # exist; a missing one is a broken state that should fail loudly.
    op.execute(sa.text("DROP INDEX idx_refresh_tokens_family_live"))
    op.execute(sa.text("ALTER TABLE user_refresh_tokens DROP COLUMN family_id"))
