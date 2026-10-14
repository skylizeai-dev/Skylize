"""Refresh-token single-use consume and POST /api/v1/auth/logout (memory backend).

Design: docs/06_integrations/myday_session_revocation_design.md. What these pin:
  * of two concurrent refreshes with ONE token, exactly one succeeds (the race
    that used to mint two live token families, design 1.0.2);
  * a rotated token cannot be refreshed again;
  * logout revokes the presented refresh token, so a later refresh is 401;
  * logout is always 204 and never an oracle (garbage, wrong type, unknown,
    already revoked all answer the same);
  * logout only ever revokes the presented token's own row.
The real-Postgres version of the race lives in
tests/integration/test_refresh_revocation_pg.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import skylize.config as config_module
from skylize.app.auth.tokens import create_refresh_token
from skylize.app.auth.user_service import InvalidCredentialsError, UserAuthService
from skylize.config import Settings
from skylize.dal.memory import InMemoryUserRepository
from skylize.edge.gateway import create_app

SECRET = "test-secret"


def _service() -> tuple[UserAuthService, InMemoryUserRepository]:
    repo = InMemoryUserRepository()
    return UserAuthService(repo, Settings(jwt_secret=SECRET)), repo


async def _logged_in(svc: UserAuthService, org: str = "org_a", email: str = "a@example.com"):
    await svc.register(org_id=org, email=email, password="hunter2pw")
    return await svc.login(email=email, password="hunter2pw")


# ── the store primitive ──────────────────────────────────────────────────────

async def test_consume_is_single_use() -> None:
    repo = InMemoryUserRepository()
    token_id, user_id = uuid4(), uuid4()
    await repo.store_refresh_token(
        token_id, user_id, datetime.now(timezone.utc) + timedelta(days=1)
    )
    assert await repo.consume_refresh_token(token_id) == user_id
    assert await repo.consume_refresh_token(token_id) is None


async def test_consume_refuses_expired_and_unknown() -> None:
    repo = InMemoryUserRepository()
    expired = uuid4()
    await repo.store_refresh_token(
        expired, uuid4(), datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    assert await repo.consume_refresh_token(expired) is None
    assert await repo.consume_refresh_token(uuid4()) is None


# ── refresh rotation ─────────────────────────────────────────────────────────

async def test_concurrent_refreshes_of_one_token_mint_exactly_one_pair() -> None:
    svc, _ = _service()
    first = await _logged_in(svc)

    results = await asyncio.gather(
        svc.refresh(refresh_token=first.refresh_token),
        svc.refresh(refresh_token=first.refresh_token),
        return_exceptions=True,
    )

    ok = [r for r in results if not isinstance(r, BaseException)]
    refused = [r for r in results if isinstance(r, InvalidCredentialsError)]
    assert len(ok) == 1, results
    assert len(refused) == 1, results


async def test_a_rotated_token_cannot_be_refreshed_again() -> None:
    svc, _ = _service()
    first = await _logged_in(svc)
    second = await svc.refresh(refresh_token=first.refresh_token)

    with pytest.raises(InvalidCredentialsError):
        await svc.refresh(refresh_token=first.refresh_token)
    # The new token from the rotation is live.
    assert (await svc.refresh(refresh_token=second.refresh_token)).access_token


async def test_refresh_refuses_an_access_token() -> None:
    svc, _ = _service()
    first = await _logged_in(svc)
    with pytest.raises(InvalidCredentialsError):
        await svc.refresh(refresh_token=first.access_token)


# ── logout ───────────────────────────────────────────────────────────────────

async def test_logout_revokes_so_a_later_refresh_is_refused() -> None:
    svc, _ = _service()
    first = await _logged_in(svc)

    assert await svc.logout(refresh_token=first.refresh_token) is True
    with pytest.raises(InvalidCredentialsError):
        await svc.refresh(refresh_token=first.refresh_token)


async def test_logout_is_idempotent() -> None:
    svc, _ = _service()
    first = await _logged_in(svc)
    assert await svc.logout(refresh_token=first.refresh_token) is True
    assert await svc.logout(refresh_token=first.refresh_token) is False


@pytest.mark.parametrize(
    "bad",
    ["", "garbage", "not.a.jwt", "eyJhbGciOiJIUzI1NiJ9.e30.xxxx"],
)
async def test_logout_never_raises_for_garbage(bad: str) -> None:
    svc, _ = _service()
    assert await svc.logout(refresh_token=bad) is False


async def test_logout_refuses_tokens_this_server_did_not_sign() -> None:
    svc, repo = _service()
    first = await _logged_in(svc)
    user = await repo.get_by_email("a@example.com")
    assert user is not None
    # A refresh token for a REAL user and a REAL stored jti would still be
    # useless if signed with any key but ours.
    forged = create_refresh_token(
        user_id=str(user.user_id), token_id=uuid4(), secret="not-the-server-key", ttl_days=1
    )
    assert await svc.logout(refresh_token=forged) is False
    # And the user's real session is untouched.
    assert (await svc.refresh(refresh_token=first.refresh_token)).access_token


async def test_logout_with_an_access_token_revokes_nothing() -> None:
    svc, _ = _service()
    first = await _logged_in(svc)
    assert await svc.logout(refresh_token=first.access_token) is False
    assert (await svc.refresh(refresh_token=first.refresh_token)).access_token


async def test_logout_only_revokes_the_presented_token() -> None:
    """User A logging out must not touch user B's session, in another org."""
    svc, _ = _service()
    a = await _logged_in(svc, org="org_a", email="a@example.com")
    b = await _logged_in(svc, org="org_b", email="b@example.com")

    assert await svc.logout(refresh_token=a.refresh_token) is True
    with pytest.raises(InvalidCredentialsError):
        await svc.refresh(refresh_token=a.refresh_token)
    assert (await svc.refresh(refresh_token=b.refresh_token)).access_token


async def test_logout_of_one_session_leaves_the_users_other_session() -> None:
    svc, _ = _service()
    first = await _logged_in(svc)
    second = await svc.login(email="a@example.com", password="hunter2pw")

    assert await svc.logout(refresh_token=first.refresh_token) is True
    assert (await svc.refresh(refresh_token=second.refresh_token)).access_token


# ── HTTP route ───────────────────────────────────────────────────────────────

@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SKYLIZE_JWT_SECRET", SECRET)
    config_module._settings = None
    with TestClient(create_app()) as c:
        yield c
    config_module._settings = None


def _login(client: TestClient) -> dict[str, str]:
    client.post(
        "/api/v1/auth/register",
        json={"org_id": "org_r", "email": "r@example.com", "password": "hunter2pw"},
    )
    resp = client.post(
        "/api/v1/auth/login", json={"email": "r@example.com", "password": "hunter2pw"}
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, str] = resp.json()
    return body


def test_route_logout_then_refresh_is_401(client: TestClient) -> None:
    pair = _login(client)

    out = client.post("/api/v1/auth/logout", json={"refresh_token": pair["refresh_token"]})
    assert out.status_code == 204
    assert out.content == b""

    again = client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert again.status_code == 401


@pytest.mark.parametrize("token", ["garbage", "not.a.jwt"])
def test_route_logout_is_204_for_garbage(client: TestClient, token: str) -> None:
    resp = client.post("/api/v1/auth/logout", json={"refresh_token": token})
    assert resp.status_code == 204


def test_route_logout_is_204_when_already_revoked(client: TestClient) -> None:
    pair = _login(client)
    body = {"refresh_token": pair["refresh_token"]}
    assert client.post("/api/v1/auth/logout", json=body).status_code == 204
    assert client.post("/api/v1/auth/logout", json=body).status_code == 204


def test_route_logout_rejects_a_malformed_body(client: TestClient) -> None:
    assert client.post("/api/v1/auth/logout", json={}).status_code == 422
    assert (
        client.post(
            "/api/v1/auth/logout", json={"refresh_token": "x", "user_id": "someone"}
        ).status_code
        == 422
    )


def test_route_logout_needs_no_access_token(client: TestClient) -> None:
    """No Authorization header at all: possession of the refresh token is the
    whole authorization, because the access token is often expired by logout."""
    pair = _login(client)
    resp = client.post("/api/v1/auth/logout", json={"refresh_token": pair["refresh_token"]})
    assert resp.status_code == 204
