"""
Integration-test fixtures: REAL Postgres + Redis, no mocks (Sprint-2 Tasks 4/5).

Every fixture here is gated on an environment variable and SKIPS the test when it
is absent, so the default `pytest` run (no infra) stays green while CI's
`integration` job — which provides service containers — actually exercises them:

    SKYLIZE_TEST_DB_URL=postgresql://skylize:localdev@localhost:5432/skylize
    SKYLIZE_TEST_REDIS_URL=redis://localhost:6379

The Postgres fixture applies Alembic migrations to a disposable schema so a run
never pollutes a shared database, and tears it down afterwards.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import Request

from skylize.edge.auth import AuthError
from skylize.schemas.base import RequestContext

DB_URL = os.getenv("SKYLIZE_TEST_DB_URL")           # admin/superuser — migrations
APP_DB_URL = os.getenv("SKYLIZE_TEST_APP_DB_URL")   # non-superuser app role — RLS-subject
REDIS_URL = os.getenv("SKYLIZE_TEST_REDIS_URL")

requires_pg = pytest.mark.skipif(not DB_URL, reason="SKYLIZE_TEST_DB_URL not set")
requires_app_role = pytest.mark.skipif(
    not (DB_URL and APP_DB_URL),
    reason="SKYLIZE_TEST_DB_URL and SKYLIZE_TEST_APP_DB_URL must both be set",
)
requires_redis = pytest.mark.skipif(not REDIS_URL, reason="SKYLIZE_TEST_REDIS_URL not set")

#: A throwaway HS256 secret. `dev_auth=False` is now mandatory on a non-memory
#: backend (config.py `_forbid_dev_auth_on_a_real_backend`), and turning dev auth
#: off makes `_require_jwt_secret_when_prod` demand a signing key. These suites
#: never mint or verify a user JWT, so the value only has to exist.
TEST_JWT_SECRET = "integration-test-jwt-secret-not-a-credential"

#: A throwaway Fernet key for the credential vault. A non-memory backend must now
#: be given one explicitly (bootstrap.py ``resolve_credential_encryption_key``);
#: the composition root no longer mints an ephemeral key behind the caller's back,
#: because on a durable backend that silently orphaned every stored credential at
#: the next restart. Fixed rather than generated so a suite that stores and reads
#: back a credential across two containers in one run stays coherent.
# Decodes to the 32 ASCII bytes b"skylize-integration-test-key!x32".
TEST_CREDENTIAL_KEY = "c2t5bGl6ZS1pbnRlZ3JhdGlvbi10ZXN0LWtleSF4MzI="


def install_dev_header_auth(app: object) -> None:
    """Authenticate a test app from X-Dev-* headers WITHOUT `settings.dev_auth`.

    These suites set ``backend="postgres"`` to exercise the real DAL, RLS, and
    governance paths -- they are not testing the auth path. `dev_auth=True` is
    refused on a non-memory backend, because `edge/auth.py:39-50` trusts
    X-Dev-Org / X-Dev-User / X-Dev-Roles verbatim: on a real backend that is not
    authentication, it is an open door to every tenant.

    So the header convenience moves INSIDE the test process, where the transport
    is in-process ASGI and no port is exposed, and the production interlock stays
    intact. Per-request org and role switching -- which the tenant-isolation
    cases depend on -- behaves exactly as `build_request_context` did.

    Both resolvers are overridden: `get_context_or_user` calls `get_context` as a
    plain function (deps.py:123), not through Depends, so overriding one does not
    cover the other.
    """
    from skylize.edge.deps import get_context, get_context_or_user

    app.dependency_overrides[get_context] = _context_from_dev_headers  # type: ignore[attr-defined]
    app.dependency_overrides[get_context_or_user] = _context_from_dev_headers  # type: ignore[attr-defined]


async def _context_from_dev_headers(request: Request) -> "RequestContext":
    """The override installed by `install_dev_header_auth`.

    Defined at MODULE level on purpose: this module uses
    `from __future__ import annotations`, so FastAPI resolves the `request`
    annotation through the function's own module globals. Declared inside the
    installer, `Request` would not be in those globals and FastAPI would treat
    `request` as a missing query parameter (422 on every call).
    """
    org = request.headers.get("X-Dev-Org")
    user = request.headers.get("X-Dev-User")
    roles_header = request.headers.get("X-Dev-Roles")
    if org is None and user is None and roles_header is None:
        # Same fail-closed shape as the real dev path: no header at all is a
        # 401, never a default owner context.
        raise AuthError("missing authentication")
    now = datetime.now(timezone.utc)
    return RequestContext(
        org_id=org or "org_dev",
        user_id=user or "user_dev",
        roles=[r.strip() for r in (roles_header or "owner").split(",") if r.strip()],
        issued_at=now,
        expires_at=now + timedelta(seconds=300),
    )


def _upgrade_head(cfg) -> None:
    """Run Alembic's upgrade in a worker thread.

    migrations/env.py calls asyncio.run() internally, which raises if invoked
    from a thread that already has a running event loop — as pytest-asyncio's
    async fixtures do. asyncio.to_thread() gives it a plain thread with none.
    """
    from alembic import command

    command.upgrade(cfg, "head")


@pytest_asyncio.fixture()
async def pg_schema() -> AsyncIterator[str]:
    """Create a throwaway schema, run migrations into it, drop it after.

    Yields the schema name. Migrations target this schema via search_path so the
    RLS policies, append-only trigger, and tables all land in an isolated space.
    """
    if not DB_URL:
        pytest.skip("SKYLIZE_TEST_DB_URL not set")

    import asyncpg
    from alembic.config import Config

    schema = f"test_{uuid.uuid4().hex[:12]}"
    conn = await asyncpg.connect(DB_URL)
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    await conn.close()

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", DB_URL)
    # Migrations honor this so DDL lands in the disposable schema.
    cfg.set_main_option("version_table_schema", schema)
    os.environ["SKYLIZE_TEST_SCHEMA"] = schema
    try:
        await asyncio.to_thread(_upgrade_head, cfg)
        yield schema
    finally:
        conn = await asyncpg.connect(DB_URL)
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await conn.close()
        os.environ.pop("SKYLIZE_TEST_SCHEMA", None)


@pytest_asyncio.fixture()
async def migrated_public() -> AsyncIterator[None]:
    """Ensure migrations are applied to the real `public` schema (admin role).

    Used by the app-role RLS tests, which must run against the same schema the
    `skylize_app` role was granted on. CI also runs `alembic upgrade head`
    beforehand; this fixture makes the dependency explicit and idempotent.
    """
    if not DB_URL:
        pytest.skip("SKYLIZE_TEST_DB_URL not set")

    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", DB_URL)
    await asyncio.to_thread(_upgrade_head, cfg)
    yield


@pytest_asyncio.fixture()
async def app_conn(migrated_public: None) -> AsyncIterator["object"]:
    """A connection as the NON-SUPERUSER `skylize_app` role (subject to RLS).

    This is the connection that proves tenant isolation actually holds: a
    superuser would bypass RLS, so isolation must be demonstrated as this role.
    """
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")

    import asyncpg

    conn = await asyncpg.connect(APP_DB_URL)
    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture()
async def admin_conn() -> AsyncIterator["object"]:
    """A connection as the admin/superuser role (seeds tenants, asserts role attrs)."""
    if not DB_URL:
        pytest.skip("SKYLIZE_TEST_DB_URL not set")

    import asyncpg

    conn = await asyncpg.connect(DB_URL)
    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture()
async def redis_client() -> AsyncIterator["object"]:
    """A flushed, isolated Redis client; FLUSHDB before and after."""
    if not REDIS_URL:
        pytest.skip("SKYLIZE_TEST_REDIS_URL not set")

    import redis.asyncio as redis

    client = redis.from_url(REDIS_URL, decode_responses=True)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()
