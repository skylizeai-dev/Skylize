"""Human-user authentication: register, login, refresh, verify."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from ...config import Settings
from ...dal.ports import RefreshTokenRow, UserRepository, UserRow
from .passwords import hash_password, verify_password
from .tokens import InvalidTokenError, create_access_token, create_refresh_token, decode_token

VALID_ROLES = frozenset({"owner", "admin", "operator", "viewer"})


class DuplicateEmailError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


@dataclass
class LoginResult:
    access_token: str
    refresh_token: str
    user: UserRow


class UserAuthService:
    def __init__(self, repo: UserRepository, settings: Settings) -> None:
        self._repo = repo
        self._settings = settings

    async def register(
        self,
        *,
        org_id: str,
        email: str,
        password: str,
        display_name: str | None = None,
    ) -> UserRow:
        existing = await self._repo.get_by_email(email)
        if existing is not None:
            raise DuplicateEmailError(f"email already registered: {email}")

        # First user in the org becomes owner; subsequent users start as viewer.
        existing_users = await self._repo.list_by_org(org_id)
        roles = ["owner"] if not existing_users else ["viewer"]

        now = datetime.now(timezone.utc)
        row = UserRow(
            user_id=uuid4(),
            org_id=org_id,
            email=email.lower().strip(),
            password_hash=hash_password(password),
            display_name=display_name,
            roles=roles,
            is_active=True,
            created_at=now,
            last_login_at=None,
        )
        await self._repo.create_user(row)
        return row

    async def login(self, *, email: str, password: str) -> LoginResult:
        user = await self._repo.get_by_email(email)
        if user is None or not verify_password(password, user.password_hash):
            raise InvalidCredentialsError("invalid email or password")
        if not user.is_active:
            raise InvalidCredentialsError("account is inactive")

        await self._repo.update_last_login(user.user_id, datetime.now(timezone.utc))
        return await self._mint_pair(user)

    async def refresh(self, *, refresh_token: str) -> LoginResult:
        try:
            claims = decode_token(refresh_token, self._settings.jwt_secret)
        except InvalidTokenError as exc:
            raise InvalidCredentialsError("invalid refresh token") from exc

        if claims.get("type") != "refresh":
            raise InvalidCredentialsError("not a refresh token")

        jti = UUID(claims["jti"])
        rt_row: RefreshTokenRow | None = await self._repo.get_refresh_token(jti)
        if rt_row is None or rt_row.revoked_at is not None:
            raise InvalidCredentialsError("refresh token revoked or not found")
        if rt_row.expires_at <= datetime.now(timezone.utc):
            raise InvalidCredentialsError("refresh token expired")

        # Rotate: revoke the consumed token before issuing the new pair.
        await self._repo.revoke_refresh_token(jti)

        user = await self._repo.get_by_id(UUID(claims["sub"]))
        if user is None or not user.is_active:
            raise InvalidCredentialsError("user not found or inactive")

        return await self._mint_pair(user)

    async def get_user(self, user_id: UUID) -> UserRow | None:
        return await self._repo.get_by_id(user_id)

    # ── internal ──────────────────────────────────────────────────────────────

    async def _mint_pair(self, user: UserRow) -> LoginResult:
        secret = self._settings.jwt_secret
        access = create_access_token(
            user_id=str(user.user_id),
            org_id=user.org_id,
            roles=user.roles,
            secret=secret,
            ttl_minutes=self._settings.jwt_access_token_ttl_minutes,
        )
        token_id = uuid4()
        refresh = create_refresh_token(
            user_id=str(user.user_id),
            token_id=token_id,
            secret=secret,
            ttl_days=self._settings.jwt_refresh_token_ttl_days,
        )
        expires_at = datetime.now(timezone.utc) + timedelta(
            days=self._settings.jwt_refresh_token_ttl_days
        )
        await self._repo.store_refresh_token(
            token_id=token_id, user_id=user.user_id, expires_at=expires_at
        )
        return LoginResult(access_token=access, refresh_token=refresh, user=user)
