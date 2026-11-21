"""
Refresh-token single-use consume and logout, against REAL Postgres.

Design: docs/06_integrations/myday_session_revocation_design.md.

WHY THIS FILE IS THE PROOF. The in-memory store cannot show the rotation race:
its `await`s never actually suspend, so two "concurrent" refreshes run one after
the other and the old read-then-revoke code passed there too. Only Postgres, with
separate pooled connections, interleaves two requests between the read and the
write. These tests fire the requests concurrently and assert exactly one wins:
  * the store primitive: 10 concurrent `consume_refresh_token` calls on one token
    -> exactly one returns the owner;
  * the HTTP route: 2 concurrent POST /refresh with one token -> one 200, one 401;
  * gate G1.6: 2 concurrent service refreshes with one token, both inside the
    rotation at once on their own pooled connections -> one valid pair, one
    plain refusal classified INSIDE the grace window, no WARNING, and no row but
    the presented token's has revoked_at set (no BFF lock is involved);
  * POST /logout revokes the presented token -> a later refresh is 401 and the
    row's revoked_at is set;
  * reuse of a rotated token is 401 while the rotated-to token still refreshes;
  * a failed INSERT during rotation rolls the consume back (Round 2 N1);
  * logout of org A's token leaves org B's session working.

Skipped unless SKYLIZE_TEST_DB_URL (+ SKYLIZE_TEST_APP_DB_URL / REDIS_URL) are set.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from skylize.app.auth import user_service
from skylize.app.auth.tokens import create_refresh_token, decode_token
from skylize.app.auth.user_service import InvalidCredentialsError, LoginResult, RefreshReplay
from skylize.bootstrap import build_container
from skylize.config import Settings
from skylize.edge.deps import get_container
from skylize.edge.errors import install_error_handlers
from skylize.edge.rate_limit import RateLimiter
from skylize.edge.routes import auth as auth_routes

from .conftest import (
    APP_DB_URL,
    DB_URL,
    REDIS_URL,
    TEST_CREDENTIAL_KEY,
    TEST_JWT_SECRET,
    purge_tenants,
    requires_app_role,
    requires_redis,
)

pytestmark = pytest.mark.integration

PASSWORD = "hunter2password"


def _gen_signing_key() -> str:
    """Throwaway governance signing key; `build_container` on postgres needs one
    (same as test_myday_identity_isolation_pg.py)."""
    from skylize.contracts.token import GOVERNANCE_CURVE
    from skylize.security.ecc_service import ECCService

    return ECCService.generate_key_pair(GOVERNANCE_CURVE).private_pem().decode()


def _orgs() -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    return f"revoke_a_{suffix}", f"revoke_b_{suffix}"


async def _seed_tenant(admin_conn: Any, org: str) -> None:
    """register needs an operator-provisioned tenants row (user_service.py:113-120)."""
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


@pytest_asyncio.fixture()
async def auth_client(
    migrated_public: None,
) -> AsyncIterator[tuple[AsyncClient, Any]]:
    """Real-Postgres app with the auth routes, run in-process on THIS loop via
    ASGITransport so the asyncpg pool and the handlers share one event loop."""
    if not (DB_URL and APP_DB_URL and REDIS_URL):
        pytest.skip("SKYLIZE_TEST_DB_URL, APP_DB_URL and REDIS_URL must all be set")

    settings = Settings(
        backend="postgres",
        dev_auth=False,
        jwt_secret=TEST_JWT_SECRET,
        credential_encryption_key=TEST_CREDENTIAL_KEY,
        db_url=DB_URL,
        db_app_url=APP_DB_URL,
        redis_url=REDIS_URL,
        governance_signing_key_pem=_gen_signing_key(),
        anthropic_api_key="",
        llm_demo_mode=True,
    )
    container = await build_container(settings)

    app = FastAPI()
    install_error_handlers(app)
    app.state.container = container
    app.state.rate_limiter = RateLimiter(10_000)
    app.state.credential_resolve_limiter = RateLimiter(10_000)
    app.dependency_overrides[get_container] = lambda: container
    app.include_router(auth_routes.router)

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, container
    finally:
        await container.aclose()


async def _register_and_login(
    client: AsyncClient, admin_conn: Any, org: str
) -> tuple[str, str, str]:
    """Return (email, access_token, refresh_token) for a fresh owner of `org`."""
    email = f"owner+{org}@example.com"
    await _seed_tenant(admin_conn, org)
    reg = await client.post(
        "/api/v1/auth/register", json={"org_id": org, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201, reg.text
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    body = login.json()
    return email, body["access_token"], body["refresh_token"]


@requires_redis
@requires_app_role
async def test_concurrent_consumes_have_exactly_one_winner(auth_client, admin_conn) -> None:
    client, container = auth_client
    org_a, _ = _orgs()
    try:
        _, _, refresh = await _register_and_login(client, admin_conn, org_a)
        claims = decode_token(refresh, TEST_JWT_SECRET)
        jti, sub = uuid.UUID(claims["jti"]), uuid.UUID(claims["sub"])
        # The repository is only reachable through the service on the container.
        repo = container.user_auth._repo

        results = await asyncio.gather(
            *(repo.consume_refresh_token(jti, sub) for _ in range(10))
        )

        assert results.count(True) == 1, results
        row = await admin_conn.fetchrow(
            "SELECT revoked_at FROM user_refresh_tokens WHERE token_id = $1", jti
        )
        assert row is not None and row["revoked_at"] is not None
    finally:
        await purge_tenants(admin_conn, [org_a])


@requires_redis
@requires_app_role
async def test_concurrent_refreshes_mint_exactly_one_pair(auth_client, admin_conn) -> None:
    client, _ = auth_client
    org_a, _ = _orgs()
    try:
        _, _, refresh = await _register_and_login(client, admin_conn, org_a)

        # The SAME refresh token, fired concurrently on one event loop.
        responses = await asyncio.gather(
            client.post("/api/v1/auth/refresh", json={"refresh_token": refresh}),
            client.post("/api/v1/auth/refresh", json={"refresh_token": refresh}),
        )

        statuses = [r.status_code for r in responses]
        assert statuses.count(200) == 1, [r.text for r in responses]
        assert statuses.count(401) == 1, [r.text for r in responses]
        winner = next(r for r in responses if r.status_code == 200).json()
        assert set(winner) == {"access_token", "refresh_token", "token_type"}, winner

        jti = uuid.UUID(decode_token(refresh, TEST_JWT_SECRET)["jti"])
        row = await admin_conn.fetchrow(
            "SELECT revoked_at FROM user_refresh_tokens WHERE token_id = $1", jti
        )
        assert row is not None and row["revoked_at"] is not None
    finally:
        await purge_tenants(admin_conn, [org_a])


def _race_rotations(monkeypatch: pytest.MonkeyPatch, repo: Any) -> list[bool]:
    """Hold each `rotate_refresh_token` call until TWO are in flight, then run
    both: each opens its own pooled connection and transaction, and Postgres's
    row lock on the presented token picks the single winner. Returns the real
    rotation outcomes, in completion order."""
    barrier = asyncio.Barrier(2)
    real = repo.rotate_refresh_token
    outcomes: list[bool] = []

    async def racing(**kwargs: Any) -> bool:
        await asyncio.wait_for(barrier.wait(), timeout=10)
        won: bool = await real(**kwargs)
        outcomes.append(won)
        return won

    monkeypatch.setattr(repo, "rotate_refresh_token", racing)
    return outcomes


def _record_verdicts(monkeypatch: pytest.MonkeyPatch) -> list[RefreshReplay | None]:
    """Record every classification the service makes of a refused refresh."""
    real = user_service.classify_refused_refresh
    seen: list[RefreshReplay | None] = []

    def spy(*args: Any, **kwargs: Any) -> RefreshReplay | None:
        verdict = real(*args, **kwargs)
        seen.append(verdict)
        return verdict

    monkeypatch.setattr(user_service, "classify_refused_refresh", spy)
    return seen


@requires_redis
@requires_app_role
async def test_pg_two_concurrent_refreshes_of_one_token_never_revoke_anything(
    auth_client, admin_conn, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Gate G1.6 against Postgres. Two service refreshes of the SAME token race
    inside `rotate_refresh_token` on two pooled connections. Correctness comes
    from the BACKEND alone (single-winner atomic rotation + the grace window);
    no BFF and no BFF lock is involved. Exactly one valid pair; the loser is the
    deterministic `InvalidCredentialsError`, classified INSIDE the grace window
    (no WARNING); no row but the presented token's has `revoked_at` set; the
    winner's new token still refreshes. Unit twin:
    `test_two_concurrent_refreshes_of_one_token_never_revoke_anything`."""
    client, container = auth_client
    svc = container.user_auth
    org_a, _ = _orgs()
    try:
        email, _, original = await _register_and_login(client, admin_conn, org_a)
        sibling = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert sibling.status_code == 200, sibling.text
        claims = decode_token(original, TEST_JWT_SECRET)
        jti, sub = uuid.UUID(claims["jti"]), uuid.UUID(claims["sub"])

        rotations = _race_rotations(monkeypatch, svc._repo)
        verdicts = _record_verdicts(monkeypatch)
        with caplog.at_level(logging.WARNING, logger="skylize.app.auth.user_service"):
            results = await asyncio.gather(
                svc.refresh(refresh_token=original),
                svc.refresh(refresh_token=original),
                return_exceptions=True,
            )
        monkeypatch.undo()

        winners = [r for r in results if isinstance(r, LoginResult)]
        losers = [r for r in results if isinstance(r, InvalidCredentialsError)]
        assert len(winners) == 1 and len(losers) == 1, results
        assert str(losers[0]) == "refresh token revoked, expired, or not found", losers
        assert sorted(rotations) == [False, True], rotations
        assert len(verdicts) == 1, verdicts
        assert verdicts[0] is not None and verdicts[0].within_grace, verdicts
        assert not [
            r for r in caplog.records if "auth_refresh_replay_after_grace" in r.getMessage()
        ]

        consumed = await admin_conn.fetch(
            "SELECT token_id FROM user_refresh_tokens "
            "WHERE user_id = $1 AND revoked_at IS NOT NULL",
            sub,
        )
        assert [r["token_id"] for r in consumed] == [jti], consumed
        live = await admin_conn.fetchval(
            "SELECT count(*) FROM user_refresh_tokens WHERE user_id = $1 AND revoked_at IS NULL",
            sub,
        )
        assert live == 2, live  # the sibling login + the winner's new token

        again = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": winners[0].refresh_token}
        )
        assert again.status_code == 200, again.text
        sib = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": sibling.json()["refresh_token"]}
        )
        assert sib.status_code == 200, sib.text
    finally:
        await purge_tenants(admin_conn, [org_a])


@requires_redis
@requires_app_role
async def test_refresh_after_logout_is_rejected(auth_client, admin_conn) -> None:
    client, _ = auth_client
    org_a, _ = _orgs()
    try:
        _, _, refresh = await _register_and_login(client, admin_conn, org_a)
        jti = uuid.UUID(decode_token(refresh, TEST_JWT_SECRET)["jti"])

        out = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
        assert out.status_code == 204, out.text

        again = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert again.status_code == 401, again.text

        row = await admin_conn.fetchrow(
            "SELECT revoked_at FROM user_refresh_tokens WHERE token_id = $1", jti
        )
        assert row is not None and row["revoked_at"] is not None
    finally:
        await purge_tenants(admin_conn, [org_a])


@requires_redis
@requires_app_role
async def test_reuse_of_rotated_token_is_rejected(auth_client, admin_conn) -> None:
    """A rotated (already-consumed) token is refused on reuse, and the reuse does
    NOT revoke the token family: the pair the rotation minted keeps working.
    Per-family revocation on reuse is a separate change (#47b, design doc 7.0 /
    9.0.4), not part of this one."""
    client, _ = auth_client
    org_a, _ = _orgs()
    try:
        _, _, original = await _register_and_login(client, admin_conn, org_a)

        first = await client.post("/api/v1/auth/refresh", json={"refresh_token": original})
        assert first.status_code == 200, first.text
        rotated = first.json()["refresh_token"]
        assert rotated != original

        reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": original})
        assert reuse.status_code == 401, reuse.text

        still_live = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": rotated}
        )
        assert still_live.status_code == 200, still_live.text
    finally:
        await purge_tenants(admin_conn, [org_a])


@requires_redis
@requires_app_role
async def test_rotation_rolls_back_the_consume_when_the_insert_fails(
    auth_client, admin_conn
) -> None:
    """Round 2 N1 against Postgres: consume + insert are ONE transaction. The new
    row's token_id is made to collide with an existing row (a primary-key
    violation on the INSERT, i.e. a failure AFTER the consume UPDATE ran); the
    old token must still be unconsumed afterwards and still refresh."""
    import asyncpg

    client, container = auth_client
    org_a, _ = _orgs()
    try:
        email, _, original = await _register_and_login(client, admin_conn, org_a)
        other = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert other.status_code == 200, other.text
        claims = decode_token(original, TEST_JWT_SECRET)
        jti, sub = uuid.UUID(claims["jti"]), uuid.UUID(claims["sub"])
        taken_id = uuid.UUID(decode_token(other.json()["refresh_token"], TEST_JWT_SECRET)["jti"])
        repo = container.user_auth._repo

        with pytest.raises(asyncpg.UniqueViolationError):
            await repo.rotate_refresh_token(
                old_token_id=jti, user_id=sub, new_token_id=taken_id,
                new_expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )

        row = await admin_conn.fetchrow(
            "SELECT revoked_at FROM user_refresh_tokens WHERE token_id = $1", jti
        )
        assert row is not None and row["revoked_at"] is None, "consume was not rolled back"
        ok = await client.post("/api/v1/auth/refresh", json={"refresh_token": original})
        assert ok.status_code == 200, ok.text
    finally:
        await purge_tenants(admin_conn, [org_a])


@requires_redis
@requires_app_role
async def test_mismatched_sub_is_refused_and_does_not_consume(auth_client, admin_conn) -> None:
    """A token whose `sub` (user A) does not own the row its `jti` names (user
    B's live token) is refused by both refresh and logout, and B's row stays
    unrevoked in Postgres. Forging that binding needs the server key; the point
    is that ownership is enforced inside the same conditional write, so the
    refusal can never revoke someone else's session as a side effect."""
    client, _ = auth_client
    org_a, org_b = _orgs()
    try:
        _, access_a, _ = await _register_and_login(client, admin_conn, org_a)
        _, _, refresh_b = await _register_and_login(client, admin_conn, org_b)
        user_a = decode_token(access_a, TEST_JWT_SECRET)["sub"]
        b_jti = uuid.UUID(decode_token(refresh_b, TEST_JWT_SECRET)["jti"])
        forged = create_refresh_token(
            user_id=user_a, token_id=b_jti, secret=TEST_JWT_SECRET, ttl_days=1
        )

        refused = await client.post("/api/v1/auth/refresh", json={"refresh_token": forged})
        assert refused.status_code == 401
        out = await client.post("/api/v1/auth/logout", json={"refresh_token": forged})
        assert out.status_code == 204

        row = await admin_conn.fetchrow(
            "SELECT revoked_at FROM user_refresh_tokens WHERE token_id = $1", b_jti
        )
        assert row is not None and row["revoked_at"] is None
        # B's genuine token still works.
        b = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_b})
        assert b.status_code == 200, b.text
    finally:
        await purge_tenants(admin_conn, [org_a, org_b])


@requires_redis
@requires_app_role
async def test_logout_revokes_and_leaves_other_org_alone(auth_client, admin_conn) -> None:
    client, _ = auth_client
    org_a, org_b = _orgs()
    try:
        _, _, refresh_a = await _register_and_login(client, admin_conn, org_a)
        _, _, refresh_b = await _register_and_login(client, admin_conn, org_b)

        out = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh_a})
        assert out.status_code == 204

        again = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_a})
        assert again.status_code == 401

        # Idempotent and not an oracle.
        repeat = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh_a})
        assert repeat.status_code == 204
        junk = await client.post("/api/v1/auth/logout", json={"refresh_token": "junk"})
        assert junk.status_code == 204

        # Org B's session is untouched.
        b = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_b})
        assert b.status_code == 200, b.text
    finally:
        await purge_tenants(admin_conn, [org_a, org_b])
