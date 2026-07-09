"""JWT creation and decoding for our own HS256 token pair."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from jose import JWTError, jwt


class InvalidTokenError(Exception):
    pass


def create_access_token(
    *,
    user_id: str,
    org_id: str,
    roles: list[str],
    secret: str,
    ttl_minutes: int,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "org_id": org_id,
        "roles": roles,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=ttl_minutes),
    }
    return str(jwt.encode(payload, secret, algorithm="HS256"))


def create_refresh_token(
    *,
    user_id: str,
    token_id: UUID,
    secret: str,
    ttl_days: int,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "jti": str(token_id),
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=ttl_days),
    }
    return str(jwt.encode(payload, secret, algorithm="HS256"))


def decode_token(token: str, secret: str) -> dict[str, Any]:
    try:
        return dict(jwt.decode(token, secret, algorithms=["HS256"]))
    except JWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
