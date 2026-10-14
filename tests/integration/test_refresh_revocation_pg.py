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
  * POST /logout revokes the presented token -> a later refresh is 401;
  * logout of org A's token leaves org B's session working.

Skipped unless SKYLIZE_TEST_DB_URL (+ SKYLIZE_TEST_APP_DB_URL / REDIS_URL) are set.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from skylize.app.auth.tokens import decode_token
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
        jti = uuid.UUID(decode_token(refresh, TEST_JWT_SECRET)["jti"])
        # The repository is only reachable through the service on the container.
        repo = container.user_auth._repo

        results = await asyncio.gather(*(repo.consume_refresh_token(jti) for _ in range(10)))

        winners = [r for r in results if r is not None]
        assert len(winners) == 1, results
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

        responses = await asyncio.gather(
            client.post("/api/v1/auth/refresh", json={"refresh_token": refresh}),
            client.post("/api/v1/auth/refresh", json={"refresh_token": refresh}),
        )

        statuses = sorted(r.status_code for r in responses)
        assert statuses == [200, 401], [r.text for r in responses]
    finally:
        await purge_tenants(admin_conn, [org_a])


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
