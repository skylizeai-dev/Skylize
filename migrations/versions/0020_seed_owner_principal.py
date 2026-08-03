"""Seed one principal + co-work manifest grant per existing org owner

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-03

Migration 0019 created `principal` / `principal_grant` and nothing has ever
written to them, so `PrincipalAuthorityService.snapshot_for` raises
`PrincipalNotFound` for every human on the platform -- correctly, since absence
of a principal record is a denial and never a grant (app/principal/provider.py:71-75).
That makes the per-employee shape unreachable rather than merely unused. This
migration gives the one human per org who unambiguously has authority a
principal record.

THE IDENTITY DECISION THIS ENCODES. `principal_id` is set to `users.user_id`
rendered as text. That is not a formatting choice, it settles an open question:
`RequestContext.user_id` is the JWT `sub` (edge/deps.py:75), which is minted from
`users.user_id` (edge/routes/auth.py:136), so a request context can be turned
into a principal lookup with no mapping table and no second identifier to keep in
sync. `principal.principal_id` is TEXT and `users.user_id` is UUID, hence the
explicit ::text. Any future provisioning path MUST use the same derivation or the
two identity spaces silently diverge.

WHY OWNERS ONLY, AND WHY DERIVED RATHER THAN LITERAL. `principal.org_id` is a FK
to `tenants(org_id)`, so a hardcoded row cannot be written -- a fresh database
has no tenants, and this migration must run on one. Rows are therefore SELECTed
from `users`, which means: on a fresh database this is a legitimate no-op, and on
a populated one every org gets exactly one principal. "Owner" is read as
`'owner' = ANY(roles)`, the same predicate the partial unique index
`users_one_owner_per_org` enforces (migration 0017), so at most one row per org
is possible by construction rather than by this query being careful.

authority_level is 'executive': an org owner sits at the top of that org's chart.
Note this governs the PRINCIPAL's position only. It cannot raise any agent's
authority -- a token's authority_level comes solely from the contract
(app/governance/authority.py:317) and the on_behalf_of claim carries no level
(contracts/token.py:120-124).

THE GRANT is exactly the co-work agent's manifest (contracts/mvp/cowork.py:54-61).
Deliberately not "every scope in the system": the seed exists to make ONE agent
usable by the person who owns the org, not to mint a superuser. Scopes live in
the same string space as ToolGrant.tool_id (app/principal/models.py:55-58), so
these ids are the ones the mint-time intersection actually compares against.

IDEMPOTENT BY CONSTRUCTION, not by Alembic running once. `principal` uses
ON CONFLICT on its (org_id, principal_id) primary key; `principal_grant` has no
natural unique key (grant_id defaults to gen_random_uuid()), so it uses
WHERE NOT EXISTS on (org_id, principal_id, scope, source). Re-running this
migration -- or running it after an operator has already provisioned the same
owner by hand -- inserts nothing and violates nothing.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Marks every row this migration owns, so downgrade removes exactly them and an
#: auditor can tell a seeded grant from one a human justified.
_CREATED_BY = "seed"

#: cowork_agent's manifest (contracts/mvp/cowork.py:54-61). Both are
#: non-irreversible: one generates text, one only reads.
_COWORK_MANIFEST = ("llm.generate", "memory.search")


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO principal (principal_id, org_id, display_name,
                                   authority_level)
            SELECT u.user_id::text, u.org_id,
                   COALESCE(NULLIF(btrim(u.display_name), ''), u.email),
                   'executive'
              FROM users u
             WHERE 'owner' = ANY (u.roles)
            ON CONFLICT (org_id, principal_id) DO NOTHING
            """
        )
    )

    for scope in _COWORK_MANIFEST:
        op.execute(
            sa.text(
                """
                INSERT INTO principal_grant (org_id, principal_id, scope, source,
                                             created_by)
                SELECT p.org_id, p.principal_id, :scope, 'position', :created_by
                  FROM principal p
                 WHERE NOT EXISTS (
                           SELECT 1 FROM principal_grant g
                            WHERE g.org_id = p.org_id
                              AND g.principal_id = p.principal_id
                              AND g.scope = :scope
                              AND g.source = 'position'
                       )
                """
            ).bindparams(scope=scope, created_by=_CREATED_BY)
        )


def downgrade() -> None:
    # Grants first: principal_grant FKs (org_id, principal_id) into principal.
    # Only rows this migration created -- an operator's hand-provisioned grant
    # carries a different created_by and survives.
    op.execute(
        sa.text("DELETE FROM principal_grant WHERE created_by = :created_by").bindparams(
            created_by=_CREATED_BY
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM principal p
             WHERE EXISTS (
                       SELECT 1 FROM users u
                        WHERE u.org_id = p.org_id
                          AND u.user_id::text = p.principal_id
                          AND 'owner' = ANY (u.roles)
                   )
               AND NOT EXISTS (
                       SELECT 1 FROM principal_grant g
                        WHERE g.org_id = p.org_id AND g.principal_id = p.principal_id
                   )
            """
        )
    )
