"""asyncpg implementation of UserRepository."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from .connection import Database
from .ports import RefreshTokenRow, UserRow


class PgUserRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create_user(self, row: UserRow) -> None:
        async with self._db.admin_session() as conn:
            await conn.execute(
                """
                INSERT INTO users
                    (user_id, org_id, email, password_hash, display_name,
                     roles, is_active, created_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                """,
                row.user_id, row.org_id, row.email, row.password_hash,
                row.display_name, row.roles, row.is_active, row.created_at,
            )

    async def create_owner_of_new_org(self, row: UserRow) -> bool:
        """Claim an org by inserting its owner, or report it already claimed.

        TWO mechanisms, both required:

        1. ``INSERT ... SELECT ... WHERE NOT EXISTS`` implements the rule the
           owner decided — registration creates a NEW org only, so an org with
           ANY existing user is refused. ``RETURNING`` makes "wrote nothing"
           observable as ``None``.
        2. The partial unique index ``users_one_owner_per_org`` (migration 0017)
           settles the race. Under READ COMMITTED the NOT EXISTS subquery takes
           no lock on rows that do not exist yet, so two concurrent registrations
           for the same NEW org can both pass it; exactly one then survives the
           index, and the loser arrives here as a unique violation.

        Only a violation of that index means "already claimed". Any other unique
        violation — notably ``users_email_unique`` — is a different condition and
        is re-raised untouched.
        """
        import asyncpg

        try:
            async with self._db.admin_session() as conn:
                written = await conn.fetchval(
                    """
                    INSERT INTO users
                        (user_id, org_id, email, password_hash, display_name,
                         roles, is_active, created_at)
                    SELECT $1,$2,$3,$4,$5,$6,$7,$8
                    WHERE NOT EXISTS (SELECT 1 FROM users WHERE org_id = $2)
                    RETURNING user_id
                    """,
                    row.user_id, row.org_id, row.email, row.password_hash,
                    row.display_name, row.roles, row.is_active, row.created_at,
                )
        except asyncpg.UniqueViolationError as exc:
            if exc.constraint_name == "users_one_owner_per_org":
                return False
            raise
        return written is not None

    async def get_by_email(self, email: str) -> UserRow | None:
        async with self._db.admin_session() as conn:
            rec = await conn.fetchrow(
                "SELECT * FROM users WHERE email = $1", email.lower()
            )
        return _row(rec) if rec else None

    async def get_by_id(self, user_id: UUID) -> UserRow | None:
        async with self._db.admin_session() as conn:
            rec = await conn.fetchrow(
                "SELECT * FROM users WHERE user_id = $1", user_id
            )
        return _row(rec) if rec else None

    async def list_by_org(self, org_id: str) -> list[UserRow]:
        async with self._db.admin_session() as conn:
            recs = await conn.fetch(
                "SELECT * FROM users WHERE org_id = $1 ORDER BY created_at", org_id
            )
        return [_row(r) for r in recs]

    async def update_last_login(self, user_id: UUID, when: datetime) -> None:
        async with self._db.admin_session() as conn:
            await conn.execute(
                "UPDATE users SET last_login_at=$2 WHERE user_id=$1", user_id, when
            )

    async def store_refresh_token(
        self, token_id: UUID, user_id: UUID, expires_at: datetime
    ) -> None:
        """A login's token: the root of a new family, `family_id = token_id`."""
        async with self._db.admin_session() as conn:
            await conn.execute(_INSERT_REFRESH_SQL, token_id, user_id, expires_at, token_id)

    async def get_refresh_token(self, token_id: UUID) -> RefreshTokenRow | None:
        async with self._db.admin_session() as conn:
            rec = await conn.fetchrow(
                "SELECT * FROM user_refresh_tokens WHERE token_id=$1", token_id
            )
        if rec is None:
            return None
        return RefreshTokenRow(
            token_id=rec["token_id"],
            user_id=rec["user_id"],
            expires_at=rec["expires_at"],
            family_id=rec["family_id"],
            revoked_at=rec["revoked_at"],
        )

    async def revoke_refresh_token(self, token_id: UUID) -> None:
        async with self._db.admin_session() as conn:
            await conn.execute(
                "UPDATE user_refresh_tokens SET revoked_at=now() WHERE token_id=$1",
                token_id,
            )

    async def consume_refresh_token(self, token_id: UUID, user_id: UUID) -> bool:
        """One conditional UPDATE decides the single winner, ownership included.

        Under READ COMMITTED, a second concurrent UPDATE of the same row waits for
        the first to commit, then re-checks `revoked_at IS NULL` against the
        committed row and matches nothing. So exactly one caller gets a row back.
        A `user_id` that does not own the row matches nothing and revokes nothing.
        """
        async with self._db.admin_session() as conn:
            consumed = await conn.fetchrow(_CONSUME_SQL, token_id, user_id)
        return consumed is not None

    async def rotate_refresh_token(
        self,
        *,
        old_token_id: UUID,
        user_id: UUID,
        new_token_id: UUID,
        new_expires_at: datetime,
    ) -> bool:
        """Consume the old token and insert the new one in ONE transaction.

        `admin_session` is a bare pool acquire that autocommits each statement
        (dal/connection.py `admin_session`), so the consume and the insert used to
        commit separately: a failure between them lost the session. Here both run
        on one connection inside `conn.transaction()`; any exception rolls the
        consume back and the old token stays live. The single-winner property is
        unchanged: a concurrent rotation's UPDATE waits on this row lock and, after
        this transaction commits, re-checks `revoked_at IS NULL` and matches
        nothing.

        Family propagation (design doc 9.1): the consume's `RETURNING family_id`
        is the family of the row it just locked and consumed, and the INSERT
        writes exactly that value, inside the same transaction. No second read,
        so no window in which another family could be picked up.
        """
        async with self._db.admin_session() as conn:
            async with conn.transaction():
                consumed = await conn.fetchrow(_CONSUME_SQL, old_token_id, user_id)
                if consumed is None:
                    return False
                await conn.execute(
                    _INSERT_REFRESH_SQL,
                    new_token_id, user_id, new_expires_at, consumed["family_id"],
                )
        return True

    async def revoke_family(self, family_id: UUID) -> int:
        """One UPDATE scoped to `family_id`; the row count is the result.

        The predicate names the family and nothing wider (no `user_id`-wide
        match), so a row of any other family is never written. `revoked_at IS
        NULL AND expires_at > now()` is the same "live" test as the consume, so
        an already consumed, revoked or expired row is left as it is and a
        repeat on the same family returns 0. Served by the partial index
        `idx_refresh_tokens_family_live` (migration 0038).
        """
        async with self._db.admin_session() as conn:
            revoked = await conn.fetch(_REVOKE_FAMILY_SQL, family_id)
        return len(revoked)


#: The single-winner consume shared by `consume_refresh_token` and
#: `rotate_refresh_token`, so the two can never drift apart. It returns the
#: consumed row's family, which the rotation copies into the new row.
_CONSUME_SQL = """
    UPDATE user_refresh_tokens
       SET revoked_at = now()
     WHERE token_id = $1
       AND user_id = $2
       AND revoked_at IS NULL
       AND expires_at > now()
    RETURNING token_id, family_id
"""

#: $4 is the family: the new token's own id for a login, the consumed row's
#: family for a rotation. Always written explicitly; the column's
#: gen_random_uuid() default (migration 0038) exists only so that app code from
#: before 0038, which omits the column, keeps working during a rolling deploy.
_INSERT_REFRESH_SQL = """
    INSERT INTO user_refresh_tokens (token_id, user_id, expires_at, family_id)
    VALUES ($1,$2,$3,$4)
"""

_REVOKE_FAMILY_SQL = """
    UPDATE user_refresh_tokens
       SET revoked_at = now()
     WHERE family_id = $1
       AND revoked_at IS NULL
       AND expires_at > now()
    RETURNING token_id
"""


def _row(rec: object) -> UserRow:
    r = dict(rec)  # type: ignore[call-overload]
    return UserRow(
        user_id=r["user_id"],
        org_id=r["org_id"],
        email=r["email"],
        password_hash=r["password_hash"],
        display_name=r.get("display_name"),
        roles=list(r["roles"]),
        is_active=r["is_active"],
        created_at=r["created_at"],
        last_login_at=r.get("last_login_at"),
    )
