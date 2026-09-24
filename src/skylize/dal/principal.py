"""asyncpg implementation of skylize.app.principal.provider.PrincipalRepository.

Mirrors PostgresJournalRepository's shape exactly (dal/work_journal.py), which
in turn mirrors PgDeliverableRepository: a `Database` dependency, one
`self._db.tenant_session(org_id)` block per method, no locally held connection
or transaction state. Backs `principal` / `principal_grant` (migration 0019),
the sibling tables of the work journal in that same migration.

THE PORT IS NOT DEFINED HERE, AND NOT IN dal/ports.py. It already exists at
app/principal/provider.py:23-36. The principal bounded context declares its own
ports in the app layer -- journal.py:37, provider.py:23, provider.py:40,
spend.py:67 -- and dal/ supplies the asyncpg side. Adding a `dal/ports.py` entry
would split one bounded context across two conventions.

READ-ONLY BY DESIGN. The port has no write method, so neither does this class.
Rows are provisioned by migration 0020 (owner principals derived from
`users`) or by an operator; there is deliberately no application write path yet.

WHY EFFECTIVE DATING IS NOT IN THE SQL. `load_grants` returns EVERY grant for
the principal and lets `compile_authority` filter with `is_active_at(at)`
(app/principal/authority.py:77, models.py:114-117). Pushing a
`valid_from <= now < valid_to` predicate down here would move the resolution
rules out of the pure kernel that exists precisely so a security reviewer can
read them without a database (app/principal/authority.py:1-17), and would make
the `at` parameter a lie -- the caller could no longer ask "what could this
person do at 09:00?" because the SQL would already have answered "now".
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from ..app.principal.models import (
    COWORK_SEED_MANIFEST,
    PRINCIPAL_SEED_CREATED_BY,
    AuthorityLevel,
    Grant,
    GrantSource,
    Principal,
)
from .connection import Database


def _principal(rec: Any) -> Principal:
    """Map a `principal` row onto the model.

    Explicit field-by-field, not `model_validate(dict(rec))`: the table carries
    `created_at`, which the model does not declare, and `Principal` is
    `extra="forbid"` (app/principal/models.py:128), so a passthrough would raise.

    `authority_level` is cast rather than validated here because the column's
    CHECK constraint (migration 0019:77-78) and the `AuthorityLevel` Literal
    (app/principal/models.py:60) carry the same five values. That agreement is
    asserted by a test rather than assumed, so the cast is checked, not trusted.
    """
    return Principal(
        principal_id=rec["principal_id"],
        org_id=rec["org_id"],
        display_name=rec["display_name"],
        position_id=rec["position_id"],
        authority_level=cast(AuthorityLevel, rec["authority_level"]),
        manager_principal_id=rec["manager_principal_id"],
        suspended_at=rec["suspended_at"],
    )


def _grant(rec: Any) -> Grant:
    """Map a `principal_grant` row onto the model.

    Drops `grant_id`, `created_by` and `created_at`: all three are provenance the
    table keeps for auditors and the pure kernel has no use for. `GrantSource` is
    an Enum, so it converts the same way `ActorKind` does in dal/work_journal.py:27.
    """
    return Grant(
        scope=rec["scope"],
        source=GrantSource(rec["source"]),
        valid_from=rec["valid_from"],
        valid_to=rec["valid_to"],
        justification=rec["justification"],
    )


class PgPrincipalRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def load_principal(
        self, *, org_id: str, principal_id: str
    ) -> Principal | None:
        async with self._db.tenant_session(org_id) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM principal WHERE org_id = $1 AND principal_id = $2",
                org_id,
                principal_id,
            )
            return None if row is None else _principal(row)

    async def load_grants(
        self, *, org_id: str, principal_id: str
    ) -> Sequence[Grant]:
        async with self._db.tenant_session(org_id) as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM principal_grant
                 WHERE org_id = $1 AND principal_id = $2
                 ORDER BY valid_from ASC, scope ASC
                """,
                org_id,
                principal_id,
            )
            return [_grant(r) for r in rows]

    async def provision_owner_principal(
        self, *, org_id: str, principal_id: str, display_name: str
    ) -> bool:
        """Provision an org owner's principal + co-work grants. Idempotent.

        Returns True when this call created the principal row, False when one was
        already present. The return value is provenance for the caller's log, not
        a success flag: False is an ordinary, expected outcome (a retry, or an
        owner migration 0020/0031 already seeded), and callers must not treat it
        as an error.

        WHY THIS IS SAFE TO RE-RUN, which is the whole point of it existing.
        Registration writes `users` and then calls this, and the two writes
        cannot share a transaction (see `tenant_session` note below). A crash
        between them must therefore be recoverable by simply calling this again.
        Both statements are conditional writes, mirroring migration 0020/0031
        exactly: `principal` uses ON CONFLICT on its (org_id, principal_id)
        primary key, and `principal_grant` -- which has no natural unique key,
        since grant_id defaults to gen_random_uuid() -- uses WHERE NOT EXISTS on
        (org_id, principal_id, scope, source). Calling this twice inserts nothing
        the second time and violates nothing.

        The grants are inserted unconditionally of whether the principal row was
        new, deliberately: a crash could have landed the principal and none of
        its grants, and a principal with no grants is a person the authority
        kernel knows but who can do nothing. Keying the grant insert off `created`
        would make that state permanent.

        WHY `tenant_session` AND NOT `admin_session`. `principal` and
        `principal_grant` carry ENABLE + FORCE ROW LEVEL SECURITY with the
        `tenant_isolation` policy (migration 0019), whose WITH CHECK requires
        `org_id = current_setting('skylize.org_id')`. `admin_session` sets no such
        GUC (dal/connection.py:83-88), and the runtime role is verified at startup
        to be neither SUPERUSER nor BYPASSRLS (bootstrap.py:291-317), so an INSERT
        issued there would be refused by the policy rather than silently
        cross-tenant. Binding the org is mandatory here, not stylistic -- and it
        is also what makes the write tenant-safe: the policy, not this method,
        guarantees the row lands in the caller's own org.

        `principal_id` MUST be `str(users.user_id)`. That derivation is fixed by
        migration 0020's identity decision and repeated in 0031; this is the
        third site bound by it, and the one an application path uses. Diverging
        here splits the identity space between registered and seeded owners.

        NO SPEND ENVELOPE. This writes `principal` and `principal_grant` only.
        A principal existing must not imply a configured budget -- `spend_envelope`
        carries a ceiling and an `over_ceiling_behavior` that are governance
        decisions someone has to actually make.
        """
        async with self._db.tenant_session(org_id) as conn:
            created = await conn.fetchval(
                """
                INSERT INTO principal (principal_id, org_id, display_name,
                                       authority_level)
                VALUES ($1, $2, $3, 'executive')
                ON CONFLICT (org_id, principal_id) DO NOTHING
                RETURNING principal_id
                """,
                principal_id,
                org_id,
                display_name,
            )

            for scope in COWORK_SEED_MANIFEST:
                await conn.execute(
                    """
                    INSERT INTO principal_grant (org_id, principal_id, scope,
                                                 source, created_by)
                    SELECT $1, $2, $3, 'position', $4
                     WHERE NOT EXISTS (
                               SELECT 1 FROM principal_grant g
                                WHERE g.org_id = $1
                                  AND g.principal_id = $2
                                  AND g.scope = $3
                                  AND g.source = 'position'
                           )
                    """,
                    org_id,
                    principal_id,
                    scope,
                    PRINCIPAL_SEED_CREATED_BY,
                )

        return created is not None
