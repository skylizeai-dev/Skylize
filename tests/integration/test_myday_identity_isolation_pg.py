"""
The My Day identity path's LIVE two-org isolation proof (design section 6.0),
exercised at the layer the repo's real infra can actually run: the BACKEND
human-user identity path (register -> login -> GET /api/v1/auth/me) against REAL
Postgres, driven by the exact access+refresh token pair the My Day cookie
carries.

WHY THIS LAYER, NOT THE BROWSER STACK
-------------------------------------
Stage 1's My Day BFF (website/src/.../api/my-day/*) is a thin encrypt/forward
shell: `POST /api/my-day/session` calls `POST /api/v1/auth/login`, stores the
returned pair in the AES-256-GCM `skylize_myday` cookie, and `GET /api/my-day/me`
forwards THAT session's access token as `Authorization: Bearer <...>` to
`GET /api/v1/auth/me` (design 2.0.2, 4.0.3). The browser -> Next -> backend
chain cannot run in CI's python job, and the Next BFF's own guarantees are
proven at the unit layer (website/src/.../my-day/*.test.ts). What only a live
Postgres can prove is the OTHER half of design 6.0: that the backend the BFF
forwards to actually isolates two orgs' identities by the access token alone.

WHAT THIS PROVES (against real Postgres)
----------------------------------------
  * Register org A's owner and org B's owner: two DIFFERENT orgs, each its own
    owner. This is the ONLY multi-tenant shape the backend allows — registration
    creates a NEW org or refuses (auth.py:96-142), there is no add-second-user
    path (design 6.0.1).
  * Log each in -> the {access_token, refresh_token} pair the My Day cookie would
    hold.
  * GET /api/v1/auth/me with A's access token returns A's org_id/user_id and
    NEVER B's; symmetrically for B. A's token presented for the read can never
    surface B's identity, because the backend keys the read off the token's own
    `sub`/`org_id` claims (deps.py:73-78) — the same fail-closed forwarding the
    My Day route relies on.
  * The access token's claims themselves bind org A to org A (decoded here), so
    the identity the read returns is the identity the token cryptographically
    asserts, not an ambient default.

HONEST SCOPE NOTE (per design 6.0.2 + repo EVIDENCE DISCIPLINE)
---------------------------------------------------------------
The backend's human-user auth repository reads/writes `users` through an ADMIN
session (dal/users.py:16-95), not the RLS-subject `skylize_app` role, so the
isolation this file proves is IDENTITY isolation enforced by the JWT claims
(each token carries only its own org), which is exactly what the My Day forward
depends on — NOT Postgres RLS on the `users` table. `GET /api/v1/auth/me`
returns only the caller's own identity and there is no org-scoped human-JWT DATA
read wired in Stage 1, so this file asserts identity (me) isolation rather than
fabricating a cross-org data read that does not exist (design 6.0 permits this
explicitly). RLS tenant-data isolation is proven separately, as the app role, in
test_postgres_isolation.py.

Skipped unless SKYLIZE_TEST_DB_URL (+ SKYLIZE_TEST_APP_DB_URL / REDIS_URL) are
set. In an environment without those (as here), it SKIPS cleanly and proves
nothing — the live path is then UNVERIFIED, per design 6.0.2.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

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
    """A throwaway ECC governance signing key PEM.

    `build_container` on a non-memory backend requires one (same as
    test_startup_role_verification_pg.py:181). This suite never governs an
    action; the key only has to be a valid PEM the composition root accepts.
    """
    from skylize.contracts.token import GOVERNANCE_CURVE
    from skylize.security.ecc_service import ECCService

    return ECCService.generate_key_pair(GOVERNANCE_CURVE).private_pem().decode()


def _orgs() -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    return f"myday_a_{suffix}", f"myday_b_{suffix}"


async def _seed_tenant(admin_conn, org: str) -> None:
    """Create the `tenants` row `register` requires before it can mint an owner.

    `users.org_id` is a FK onto `tenants` (migration 0001), and the register
    route does NOT create the tenant — it requires an operator-provisioned one to
    exist already (user_service.py:113-120). Without this seed the register POST
    raises ForeignKeyViolationError on `users_org_id_fkey` and 500s, which is
    exactly what the first CI run of this file hit. Seeded via the admin
    connection (no RLS on `tenants`), ON CONFLICT DO NOTHING so a re-run is safe.
    Mirrors test_postgres_isolation.py:36-41.
    """
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


@pytest_asyncio.fixture()
async def myday_client(
    migrated_public: None,
) -> AsyncIterator[tuple[AsyncClient, object]]:
    """A real-Postgres app exposing the human-user auth routes, plus the built
    container (so tests can tear tenants down).

    The container is built on the postgres backend with the test JWT secret, so
    `get_current_user` (deps.py:68) verifies with the SAME key the routes mint
    with — exercising the genuine login -> JWT -> /me path, NOT dev headers.

    httpx AsyncClient + ASGITransport runs the app IN-PROCESS on THIS event loop,
    so the container's asyncpg pool (created on this loop) matches the loop the
    handlers run on — the same reason test_agent_execute_governed_e2e.py uses it
    over starlette's TestClient (which uses a separate portal loop).
    """
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
    # Route both container readers at the real PG container. NO dev-header
    # override: this suite exercises the genuine Bearer-JWT auth path.
    app.dependency_overrides[get_container] = lambda: container
    app.include_router(auth_routes.router)

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, container
    finally:
        await container.aclose()


async def _register(client: AsyncClient, org: str, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"org_id": org, "email": email, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["org_id"] == org
    assert body["roles"] == ["owner"]


async def _login(client: AsyncClient, email: str) -> tuple[str, str]:
    """Return exactly the (access_token, refresh_token) pair the My Day cookie
    would carry."""
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["access_token"], body["refresh_token"]


async def _me(client: AsyncClient, access_token: str):
    return await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"}
    )


@requires_redis
@requires_app_role
async def test_two_org_identity_isolation_live(myday_client, admin_conn) -> None:
    """The end-to-end design-6.0 assertion against real Postgres: A's session
    token sees only A's identity, B's only B's, and neither can see the other."""
    client, _ = myday_client
    org_a, org_b = _orgs()
    email_a = f"owner+{org_a}@example.com"
    email_b = f"owner+{org_b}@example.com"
    try:
        # Two different orgs, each its own owner (the only multi-tenant shape).
        # The `tenants` rows must exist first: register mints an owner but does
        # NOT create the tenant (user_service.py:113-120).
        await _seed_tenant(admin_conn, org_a)
        await _seed_tenant(admin_conn, org_b)
        await _register(client, org_a, email_a)
        await _register(client, org_b, email_b)

        access_a, refresh_a = await _login(client, email_a)
        access_b, refresh_b = await _login(client, email_b)

        # The pair the cookie carries is well-formed and the two orgs' tokens
        # differ (no accidental shared credential).
        assert access_a and refresh_a and access_b and refresh_b
        assert access_a != access_b
        assert refresh_a != refresh_b

        # The token's OWN claims bind it to its org — the identity is asserted by
        # the credential, not by an ambient default.
        claims_a = decode_token(access_a, TEST_JWT_SECRET)
        claims_b = decode_token(access_b, TEST_JWT_SECRET)
        assert claims_a["org_id"] == org_a
        assert claims_b["org_id"] == org_b
        assert claims_a["type"] == "access"

        # A's session token -> A's identity, and NEVER B's.
        resp_a = await _me(client, access_a)
        assert resp_a.status_code == 200, resp_a.text
        body_a = resp_a.json()
        assert body_a["org_id"] == org_a
        assert body_a["email"] == email_a
        assert body_a["org_id"] != org_b
        assert body_a["email"] != email_b

        # B's session token -> B's identity, and NEVER A's.
        resp_b = await _me(client, access_b)
        assert resp_b.status_code == 200, resp_b.text
        body_b = resp_b.json()
        assert body_b["org_id"] == org_b
        assert body_b["email"] == email_b
        assert body_b["org_id"] != org_a
        assert body_b["email"] != email_a

        # The two user_ids are distinct: no cross-identity collision.
        assert body_a["user_id"] != body_b["user_id"]
    finally:
        await purge_tenants(admin_conn, [org_a, org_b])


@requires_redis
@requires_app_role
async def test_missing_and_invalid_bearer_are_fail_closed(myday_client, admin_conn) -> None:
    """The fail-closed lifecycle the My Day cookie mirrors (design 5.0): a
    missing or invalid access token is 401 at the backend the BFF forwards to,
    so a My Day session that decrypts to a dead token gets a clean 401 too."""
    client, _ = myday_client
    org_a, _ = _orgs()
    email_a = f"owner+{org_a}@example.com"
    try:
        await _seed_tenant(admin_conn, org_a)
        await _register(client, org_a, email_a)
        access_a, _ = await _login(client, email_a)

        # No Authorization header at all.
        no_header = await client.get("/api/v1/auth/me")
        assert no_header.status_code == 401

        # A structurally-invalid / forged token.
        garbage = await _me(client, "not.a.real.jwt")
        assert garbage.status_code == 401

        # A token signed with the WRONG secret must not verify (cross-secret
        # isolation at the JWT layer — the analogue of the cookie's AES key
        # isolation proven in myday-session.test.ts).
        from skylize.app.auth.tokens import create_access_token

        forged = create_access_token(
            user_id=str(uuid.uuid4()),
            org_id=org_a,
            roles=["owner"],
            secret="a-totally-different-secret-not-the-server-key",
            ttl_minutes=30,
        )
        forged_resp = await _me(client, forged)
        assert forged_resp.status_code == 401

        # The genuine token still works — the 401s above are real rejections,
        # not a broken route.
        ok = await _me(client, access_a)
        assert ok.status_code == 200
    finally:
        await purge_tenants(admin_conn, [org_a])
