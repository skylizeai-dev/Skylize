"""Refresh-token single-use consume and POST /api/v1/auth/logout (memory backend).

Design: docs/06_integrations/myday_session_revocation_design.md. What these pin:
  * of two concurrent refreshes with ONE token, exactly one succeeds (the race
    that used to mint two live token families, design 1.0.2);
  * a rotated token cannot be refreshed again;
  * logout revokes the presented refresh token, so a later refresh is 401;
  * logout is always 204 and never an oracle (garbage, wrong type, unknown,
    already revoked all answer the same);
  * logout only ever revokes the presented token's own row;
  * logout is a non-2xx when the revoke write itself fails (the BFF fails closed);
  * POST /refresh answers one fixed 401 detail for every refusal reason;
  * Settings refuses an access-token TTL above 15 minutes (OD3-AMEND).
The real-Postgres version of the race lives in
tests/integration/test_refresh_revocation_pg.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import skylize.config as config_module
from skylize.app.auth.tokens import create_refresh_token, decode_token
from skylize.app.auth.user_service import InvalidCredentialsError, UserAuthService
from skylize.config import Settings
from skylize.dal.memory import InMemoryUserRepository
from skylize.edge.gateway import create_app
from skylize.edge.routes.auth import REFRESH_REFUSED_DETAIL

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
    assert await repo.consume_refresh_token(token_id, user_id) is True
    assert await repo.consume_refresh_token(token_id, user_id) is False


async def test_consume_refuses_expired_and_unknown() -> None:
    repo = InMemoryUserRepository()
    expired, owner = uuid4(), uuid4()
    await repo.store_refresh_token(
        expired, owner, datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    assert await repo.consume_refresh_token(expired, owner) is False
    assert await repo.consume_refresh_token(uuid4(), owner) is False


async def test_consume_by_a_non_owner_refuses_and_leaves_the_row_live() -> None:
    repo = InMemoryUserRepository()
    token_id, owner, stranger = uuid4(), uuid4(), uuid4()
    await repo.store_refresh_token(
        token_id, owner, datetime.now(timezone.utc) + timedelta(days=1)
    )
    assert await repo.consume_refresh_token(token_id, stranger) is False
    row = await repo.get_refresh_token(token_id)
    assert row is not None and row.revoked_at is None
    # The real owner can still use it.
    assert await repo.consume_refresh_token(token_id, owner) is True


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


async def _mismatched_token(svc: UserAuthService, repo: InMemoryUserRepository):
    """User A's `sub` bound to user B's live `jti`, signed with the REAL key.

    A refresh token is a bearer credential: B's genuine token presented by
    anyone simply refreshes as B, and no check can tell the holder apart. What
    the server CAN enforce is that a token's `sub` owns the row its `jti`
    names. This forges exactly that mismatch (it needs the server secret, which
    only a test or a key compromise has) and returns (token, b_refresh, b_jti).
    """
    from skylize.app.auth.tokens import decode_token

    await _logged_in(svc, org="org_a", email="a@example.com")
    b = await _logged_in(svc, org="org_b", email="b@example.com")
    user_a = await repo.get_by_email("a@example.com")
    assert user_a is not None
    b_jti = decode_token(b.refresh_token, SECRET)["jti"]
    forged = create_refresh_token(
        user_id=str(user_a.user_id), token_id=UUID(b_jti), secret=SECRET, ttl_days=1
    )
    return forged, b.refresh_token, b_jti


async def test_refresh_refuses_a_sub_that_does_not_own_the_jti_without_consuming_it() -> None:
    svc, repo = _service()
    forged, b_refresh, b_jti = await _mismatched_token(svc, repo)

    with pytest.raises(InvalidCredentialsError):
        await svc.refresh(refresh_token=forged)

    # B's row was NOT consumed: still live, and B can still refresh with it.
    row = await repo.get_refresh_token(UUID(b_jti))
    assert row is not None and row.revoked_at is None
    assert (await svc.refresh(refresh_token=b_refresh)).access_token


async def test_logout_with_a_mismatched_sub_does_not_revoke_the_other_users_token() -> None:
    svc, repo = _service()
    forged, b_refresh, b_jti = await _mismatched_token(svc, repo)

    assert await svc.logout(refresh_token=forged) is False
    row = await repo.get_refresh_token(UUID(b_jti))
    assert row is not None and row.revoked_at is None
    assert (await svc.refresh(refresh_token=b_refresh)).access_token


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


def test_route_refresh_401_detail_is_identical_for_every_refusal(client: TestClient) -> None:
    """POST /refresh is not an oracle: whatever the reason, the 401 body is the
    same fixed detail, and it never echoes the presented token."""
    pair = _login(client)
    sub = decode_token(pair["access_token"], SECRET)["sub"]

    bad_signature = create_refresh_token(
        user_id=sub, token_id=uuid4(), secret="not-the-server-key", ttl_days=1
    )
    unknown_jti = create_refresh_token(
        user_id=sub, token_id=uuid4(), secret=SECRET, ttl_days=1
    )
    revoked = pair["refresh_token"]
    assert client.post("/api/v1/auth/logout", json={"refresh_token": revoked}).status_code == 204

    cases = {
        "bad_signature": bad_signature,
        "access_token_as_refresh": pair["access_token"],
        "revoked": revoked,
        "unknown_jti": unknown_jti,
        "garbage": "garbage",
    }
    details: dict[str, object] = {}
    for name, token in cases.items():
        resp = client.post("/api/v1/auth/refresh", json={"refresh_token": token})
        assert resp.status_code == 401, (name, resp.text)
        body = resp.json()
        assert set(body) == {"detail"}, (name, body)
        assert token not in resp.text, name
        details[name] = body["detail"]

    assert set(details.values()) == {REFRESH_REFUSED_DETAIL}, details


@pytest.fixture()
def client_no_raise(monkeypatch: pytest.MonkeyPatch):
    """Like `client`, but a server error comes back as the HTTP response the
    caller would see instead of being re-raised into the test."""
    monkeypatch.setenv("SKYLIZE_JWT_SECRET", SECRET)
    config_module._settings = None
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c
    config_module._settings = None


def test_route_logout_is_non_2xx_when_the_revoke_write_fails(
    client_no_raise: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The BFF fails closed on a non-2xx logout. A failed DB write must surface
    as one, never be swallowed into the "nothing to revoke" 204."""
    pair = _login(client_no_raise)
    repo = client_no_raise.app.state.container.user_auth._repo  # type: ignore[attr-defined]

    async def _boom(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(repo, "consume_refresh_token", _boom)

    resp = client_no_raise.post(
        "/api/v1/auth/logout", json={"refresh_token": pair["refresh_token"]}
    )
    assert not 200 <= resp.status_code < 300, resp.status_code
    assert resp.status_code >= 500


# ── access-token TTL cap (owner ruling OD3-AMEND) ────────────────────────────

@pytest.fixture()
def no_ttl_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SKYLIZE_JWT_ACCESS_TOKEN_TTL_MINUTES", raising=False)


@pytest.mark.parametrize("ttl", [16, 30])
def test_settings_refuses_an_access_token_ttl_above_15(no_ttl_env: None, ttl: int) -> None:
    with pytest.raises(ValidationError, match="JWT_ACCESS_TOKEN_TTL_MINUTES"):
        Settings(jwt_secret=SECRET, jwt_access_token_ttl_minutes=ttl)


def test_settings_refuses_an_access_token_ttl_above_15_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SKYLIZE_JWT_ACCESS_TOKEN_TTL_MINUTES", "30")
    with pytest.raises(ValidationError):
        Settings(jwt_secret=SECRET)


def test_settings_accepts_15_and_the_default_is_within_the_cap(no_ttl_env: None) -> None:
    assert Settings(jwt_secret=SECRET, jwt_access_token_ttl_minutes=15).jwt_access_token_ttl_minutes == 15
    default = Settings(jwt_secret=SECRET).jwt_access_token_ttl_minutes
    assert default <= 15
    assert Settings.model_fields["jwt_access_token_ttl_minutes"].default <= 15
