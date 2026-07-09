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
        async with self._db.admin_session() as conn:
            await conn.execute(
                """
                INSERT INTO user_refresh_tokens (token_id, user_id, expires_at)
                VALUES ($1,$2,$3)
                """,
                token_id, user_id, expires_at,
            )

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
            revoked_at=rec["revoked_at"],
        )

    async def revoke_refresh_token(self, token_id: UUID) -> None:
        async with self._db.admin_session() as conn:
            await conn.execute(
                "UPDATE user_refresh_tokens SET revoked_at=now() WHERE token_id=$1",
                token_id,
            )


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
