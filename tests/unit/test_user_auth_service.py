"""UserAuthService: register → login → JWT claims → refresh rotation."""

from __future__ import annotations

import pytest

from skylize.app.auth.tokens import InvalidTokenError, decode_token
from skylize.app.auth.user_service import (
    DuplicateEmailError,
    InvalidCredentialsError,
    UserAuthService,
)
from skylize.config import Settings
from skylize.dal.memory import InMemoryUserRepository


def _service() -> UserAuthService:
    settings = Settings(jwt_secret="test-secret", jwt_access_token_ttl_minutes=15)
    return UserAuthService(InMemoryUserRepository(), settings)


async def test_register_first_user_is_owner() -> None:
    svc = _service()
    user = await svc.register(org_id="org_a", email="a@example.com", password="hunter2pw")
    assert user.roles == ["owner"]
    assert user.org_id == "org_a"
    assert user.password_hash != "hunter2pw"


async def test_register_second_user_is_viewer() -> None:
    svc = _service()
    await svc.register(org_id="org_a", email="a@example.com", password="hunter2pw")
    second = await svc.register(org_id="org_a", email="b@example.com", password="hunter2pw")
    assert second.roles == ["viewer"]


async def test_register_duplicate_email_rejected() -> None:
    svc = _service()
    await svc.register(org_id="org_a", email="a@example.com", password="hunter2pw")
    with pytest.raises(DuplicateEmailError):
        await svc.register(org_id="org_b", email="a@example.com", password="other_pw")


async def test_login_returns_valid_jwt_with_correct_claims() -> None:
    svc = _service()
    user = await svc.register(org_id="org_a", email="a@example.com", password="hunter2pw")
    result = await svc.login(email="a@example.com", password="hunter2pw")
    claims = decode_token(result.access_token, "test-secret")
    assert claims["sub"] == str(user.user_id)
    assert claims["org_id"] == "org_a"
    assert claims["roles"] == ["owner"]
    assert claims["type"] == "access"


async def test_login_wrong_password_rejected() -> None:
    svc = _service()
    await svc.register(org_id="org_a", email="a@example.com", password="hunter2pw")
    with pytest.raises(InvalidCredentialsError):
        await svc.login(email="a@example.com", password="wrong_password")


async def test_login_unknown_email_rejected() -> None:
    svc = _service()
    with pytest.raises(InvalidCredentialsError):
        await svc.login(email="nobody@example.com", password="whatever1")


async def test_refresh_token_rotation_works() -> None:
    svc = _service()
    await svc.register(org_id="org_a", email="a@example.com", password="hunter2pw")
    first = await svc.login(email="a@example.com", password="hunter2pw")

    rotated = await svc.refresh(refresh_token=first.refresh_token)
    # Refresh tokens embed a unique jti so they always differ.
    assert rotated.refresh_token != first.refresh_token
    # Access token claims must decode correctly.
    claims = decode_token(rotated.access_token, "test-secret")
    assert claims["type"] == "access"

    # The original refresh token is now revoked — reuse must fail.
    with pytest.raises(InvalidCredentialsError):
        await svc.refresh(refresh_token=first.refresh_token)


async def test_expired_access_token_rejected_by_decode() -> None:
    settings = Settings(jwt_secret="test-secret", jwt_access_token_ttl_minutes=-1)
    svc = UserAuthService(InMemoryUserRepository(), settings)
    await svc.register(org_id="org_a", email="a@example.com", password="hunter2pw")
    result = await svc.login(email="a@example.com", password="hunter2pw")

    with pytest.raises(InvalidTokenError):
        decode_token(result.access_token, "test-secret")
