"""Backfill principal + co-work grants for owners migration 0020 could not see

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-19

WHY A SECOND SEED EXISTS AT ALL. Migration 0020 already contains exactly this
INSERT, and it is correct. It ran on a database where `users` was still empty:
both org owners on the live deployment registered AFTER 0020 had been applied,
so its `SELECT ... FROM users WHERE 'owner' = ANY(roles)` matched zero rows and
it committed as a legitimate no-op. Alembic then recorded 0020 as applied and
will never run it again. The result observed on the live dev database is a
`principal` table with zero rows while two owners exist -- so
`PrincipalAuthorityService.snapshot_for` raises `PrincipalNotFound` for both
(app/principal/provider.py:71-75), which is a denial, and the co-work surface is
unreachable for every human on the platform.

This migration is therefore NOT a correction of 0020. It is the same seed
re-executed at a point in time when the rows it selects from actually exist.
That is also why it is a migration rather than a one-off operator script: the
same ordering hazard applies to any environment provisioned in the same order
(migrate first, register second), and a migration is the only artifact that is
guaranteed to run exactly once per database, in order, on every environment.

DERIVATION IS COPIED, NOT REINVENTED. `principal_id` is `users.user_id::text`,
identical to 0020, which states the rule explicitly: `RequestContext.user_id` is
the JWT `sub` (edge/deps.py:75), minted from `users.user_id`
(edge/routes/auth.py:136), so a request context becomes a principal lookup with
no mapping table. 0020's warning -- "Any future provisioning path MUST use the
same derivation or the two identity spaces silently diverge" -- binds this
migration and the application write path added alongside it
(dal/principal.py `provision_owner_principal`). All three now share one rule.

THE GRANT is the co-work agent's manifest and nothing more:
`{llm.generate, memory.search}` (contracts/mvp/cowork.py:56-63), matching 0020
byte for byte. Both are non-irreversible -- one generates text, one only reads.
This seed makes ONE agent usable by the person who owns the org; it does not
mint a superuser.

NO SPEND ENVELOPE, DELIBERATELY. This migration does not write to
`spend_envelope` and must never be extended to. A principal existing means "this
person is known to the authority kernel and has these two scopes". It must not
imply that a budget has been configured: `spend_envelope` is money, it carries
an `over_ceiling_behavior` that is a governance decision in its own right
(migration 0019), and seeding one would hand out a default budget nobody
authorised. Co-work's two scopes need no envelope to function.

IDEMPOTENT BY CONSTRUCTION, and re-verified rather than inherited. `principal`
uses ON CONFLICT on its (org_id, principal_id) primary key; `principal_grant`
has no natural unique key (grant_id defaults to gen_random_uuid()), so it uses
WHERE NOT EXISTS on (org_id, principal_id, scope, source). An owner already
provisioned -- by 0020 on a database where it did see rows, by an operator by
hand, or by the new registration write path -- is skipped, not duplicated. This
matters more here than it did in 0020: this deployment has already demonstrated
that a migration can run against a table whose expected rows do not exist yet,
so neither a clean slate nor a fully-populated one may be assumed.

SCOPE IS OWNERS, exactly as 0020. `'owner' = ANY(u.roles)` is the same predicate
the partial unique index `users_one_owner_per_org` enforces (migration 0017), so
at most one row per org is possible by construction. Non-owner users are not
given principals here; that is a separate, deliberate provisioning decision.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Marks every row this migration owns. Identical to 0020's marker on purpose:
#: these rows are the same KIND of row -- a platform-provisioned seed rather
#: than a human-justified grant -- and an auditor reading `created_by` should
#: not have to know which of two migrations happened to win the race to insert.
_CREATED_BY = "seed"

#: cowork_agent's manifest (contracts/mvp/cowork.py:56-63), same as 0020.
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
    """Intentionally a no-op.

    This migration and 0020 write rows that are indistinguishable by design
    (same derivation, same scopes, same `created_by`). Deleting "the rows 0031
    created" is therefore not expressible: on a database where 0020 already
    seeded an owner, 0031 inserted nothing for them, and a downgrade that
    deleted by `created_by = 'seed'` would remove 0020's rows instead -- taking
    away authority this migration never granted.

    0020's own downgrade already removes every seeded principal and grant. A
    downgrade past this revision to 0020 is the correct way to undo the seed,
    and it remains available. Doing nothing here is what keeps that true.
    """
