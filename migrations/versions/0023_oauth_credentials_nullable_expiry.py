"""oauth_credentials.expires_at: relax NOT NULL to allow non-expiring grants

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-03

WHY. Migration 0021 declared ``expires_at TIMESTAMPTZ NOT NULL`` because every
provider the infrastructure had then (Google Drive, later Asana) issues a
relative-expiry access token: Drive and Asana both return ``expires_in`` on the
RFC 6749 token response, so a real timestamp always existed to store.

Notion does not. Its documented token response carries NO ``expires_in`` field
(`docs/audits/audit_notion_asana_readiness.md` 0.1, live-verified 2026-09-02
against developers.notion.com/reference/create-a-token and /refresh-a-token), so
there is no honest value to put in this column. The three options were a
far-future sentinel, a guessed TTL, or NULL. This migration takes NULL.

WHY NOT A SENTINEL OR A GUESSED TTL. A sentinel ('9999-12-31') is a lie that
every future reader has to know about, and it silently re-enters the comparison
at ``evaluate_grant`` as though it were a real expiry. A guessed TTL is worse: it
would trigger a refresh the provider never asked for, and — because a wrong guess
is indistinguishable from a real expiry — it would make a healthy grant look dead
on a schedule we invented. NULL says exactly what is true: this grant carries no
expiry, so time is not a freshness signal for it.

WHAT NULL MEANS, PRECISELY. ``expires_at IS NULL`` = "this grant does not expire
by time." It does NOT mean "unknown" and it does NOT mean "expired". The freshness
evaluator (`app/credentials/oauth.py` ``evaluate_grant``) treats NULL as
never-stale-by-clock, and the grant's liveness is then established ONLY by
``connection_state`` — which the provider's own signals write.

THE CONSEQUENCE, STATED PLAINLY BECAUSE IT IS A REAL GAP. For a non-expiring
grant no refresh is ever attempted, so the refresh-failure path that currently
detects revocation (``_classify_failure`` -> ``is_revocation_error`` ->
``GrantRevoked``) is NEVER reached. Revocation of such a grant can only be
observed when a live API call fails. ``OAuthCredentialService.mark_revoked_by_provider``
is added in the same pass as the primitive a connector calls on an unambiguous
401/403 to write that state. A connector that never calls it will keep a dead
grant marked 'valid' forever. See that method's docstring.

NO OTHER INVARIANT DEPENDS ON THIS COLUMN. Verified against 0021 at this commit:
the only CHECK constraint is ``oauth_credentials_state_check`` on
``connection_state``; the two indexes are ``idx_oauth_credentials_unique``
(org_id, provider, label) and ``idx_oauth_credentials_org_provider``
(org_id, provider). Neither references ``expires_at``, so dropping NOT NULL
invalidates no index and weakens no uniqueness or tenancy guarantee. RLS is
untouched.

EXISTING ROWS ARE UNAFFECTED. Dropping a NOT NULL constraint widens the accepted
domain; it rewrites nothing and every Drive/Asana row keeps the real timestamp it
already has. This migration is therefore safe to apply online, and its downgrade
is only safe while no NULL row exists (see ``downgrade``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE oauth_credentials ALTER COLUMN expires_at DROP NOT NULL"
    ))

    op.execute(sa.text("COMMENT ON COLUMN oauth_credentials.expires_at IS "
                       "'When the stored access token goes stale. NULL means the "
                       "grant does not expire by time (e.g. Notion), NOT that the "
                       "expiry is unknown: such a grant is never stale by clock and "
                       "its liveness is carried entirely by connection_state.'"))


def downgrade() -> None:
    """Restore NOT NULL.

    This FAILS if any non-expiring grant exists, and that failure is correct
    rather than something to code around: there is no honest timestamp to
    backfill a NULL with. An operator rolling back past this migration must first
    decide what to do with those grants (delete them, or have their customers
    reconnect against a provider that does issue an expiry).
    """
    op.execute(sa.text(
        "ALTER TABLE oauth_credentials ALTER COLUMN expires_at SET NOT NULL"
    ))
