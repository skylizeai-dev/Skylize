"""UserAuthService: register → login → JWT claims → refresh rotation."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from skylize.app.auth.tokens import InvalidTokenError, decode_token
from skylize.app.auth.user_service import (
    DuplicateEmailError,
    InvalidCredentialsError,
    OrgNotAvailableError,
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


async def test_register_into_an_existing_org_is_refused() -> None:
    """The security fix. This case used to admit the second registrant as
    `viewer` — a role that reads GET /api/v1/agents and every deliverable route —
    so a stranger who knew an org_id could read that tenant's deliverables.
    Registration is unauthenticated, so it now creates new orgs only."""
    svc = _service()
    await svc.register(org_id="org_a", email="a@example.com", password="hunter2pw")
    with pytest.raises(OrgNotAvailableError):
        await svc.register(org_id="org_a", email="b@example.com", password="hunter2pw")


async def test_refusal_does_not_write_the_second_user() -> None:
    """A refused registration must leave no account behind — not even an inert
    one that a later change could grant a role to."""
    repo = InMemoryUserRepository()
    svc = UserAuthService(repo, Settings(jwt_secret="test-secret"))
    await svc.register(org_id="org_a", email="a@example.com", password="hunter2pw")
    with pytest.raises(OrgNotAvailableError):
        await svc.register(org_id="org_a", email="b@example.com", password="hunter2pw")

    assert await repo.get_by_email("b@example.com") is None
    assert len(await repo.list_by_org("org_a")) == 1


async def test_registration_still_creates_a_brand_new_org() -> None:
    """The refusal is scoped to occupied orgs; a fresh org_id still works."""
    svc = _service()
    await svc.register(org_id="org_a", email="a@example.com", password="hunter2pw")
    second = await svc.register(org_id="org_b", email="b@example.com", password="hunter2pw")
    assert second.roles == ["owner"]
    assert second.org_id == "org_b"


async def test_a_second_user_can_still_be_written_through_the_repository() -> None:
    """The route is closed; the repository is not. Adding a second user is a
    deliberate, authenticated action (a future invite flow), so the unconditional
    `create_user` write stays available for it and for test setup."""
    repo = InMemoryUserRepository()
    svc = UserAuthService(repo, Settings(jwt_secret="test-secret"))
    owner = await svc.register(org_id="org_a", email="a@example.com", password="hunter2pw")

    await repo.create_user(
        replace(owner, user_id=uuid4(), email="viewer@example.com", roles=["viewer"])
    )
    assert len(await repo.list_by_org("org_a")) == 2


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
