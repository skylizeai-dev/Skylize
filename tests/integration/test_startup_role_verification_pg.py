"""
Startup app-role verification (bootstrap.verify_app_role_is_rls_subject) —
REAL Postgres, no mocks.

The Settings interlock (config.py `_require_distinct_app_dsn_on_a_real_backend`)
is a raw string comparison and cannot catch a respelled superuser DSN
(localhost vs 127.0.0.1, an added query parameter, postgres:// vs
postgresql://). These tests prove the authoritative startup check — asking
pg_roles as the configured role itself — actually holds:

  - the genuine skylize_app role passes, and is PROVEN non-superuser /
    non-BYPASSRLS from pg_roles first (same probe as test_postgres_isolation);
  - a SUPERUSER runtime role is refused with an error naming the role and the
    attribute, and saying RLS would be silently inert;
  - a NOSUPERUSER role with only BYPASSRLS is refused too, naming BYPASSRLS;
  - an unreachable database surfaces as a connection error, never as the typed
    ConfigurationError — a down database is not an RLS finding;
  - end to end: build_container refuses a respelled superuser app DSN that the
    Settings string interlock cannot catch.

Skipped unless SKYLIZE_TEST_DB_URL (+ APP_DB_URL / REDIS_URL where marked) are set.
"""

from __future__ import annotations

import asyncio
import uuid
from urllib.parse import urlsplit

import pytest

from skylize.bootstrap import (
    ConfigurationError,
    build_container,
    verify_app_role_is_rls_subject,
)
from skylize.config import Settings
from skylize.dal.connection import Database

from .conftest import (
    APP_DB_URL,
    DB_URL,
    REDIS_URL,
    TEST_CREDENTIAL_KEY,
    TEST_JWT_SECRET,
    requires_app_role,
    requires_pg,
    requires_redis,
)

pytestmark = pytest.mark.integration


def _dsn_for_role(base_dsn: str, user: str, password: str) -> str:
    """The base DSN's host/port/database with a different login role."""
    parts = urlsplit(base_dsn)
    host = parts.hostname or "localhost"
    port = parts.port or 5432
    return f"{parts.scheme}://{user}:{password}@{host}:{port}{parts.path}"


def _respell(dsn: str) -> str:
    """The SAME server under a different spelling — defeats raw string equality."""
    if "localhost" in dsn:
        return dsn.replace("localhost", "127.0.0.1")
    if "127.0.0.1" in dsn:
        return dsn.replace("127.0.0.1", "localhost")
    sep = "&" if "?" in dsn else "?"
    return f"{dsn}{sep}application_name=respelled_probe"


# ---------------------------------------------------------------------------
# The app role passes — proven from pg_roles, not assumed
# ---------------------------------------------------------------------------

@requires_app_role
async def test_app_role_is_proven_rls_subject_and_passes(admin_conn) -> None:
    """Ground truth first (same probe as test_postgres_isolation.py:67-72):
    the test app role is genuinely NOSUPERUSER + NOBYPASSRLS, so its passing
    the startup check means something."""
    db = Database(APP_DB_URL)
    await db.connect()
    try:
        async with db.pool.acquire() as conn:
            role = await conn.fetchval("SELECT current_user")
        row = await admin_conn.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = $1", role
        )
        assert row["rolsuper"] is False, f"{role} is a superuser — fixture role is wrong"
        assert row["rolbypassrls"] is False, f"{role} has BYPASSRLS — fixture role is wrong"

        await verify_app_role_is_rls_subject(db)  # must not raise
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Disqualified roles are refused, naming the attribute
# ---------------------------------------------------------------------------

@requires_pg
async def test_superuser_runtime_role_is_refused(admin_conn) -> None:
    db = Database(DB_URL)
    await db.connect()
    try:
        async with db.pool.acquire() as conn:
            role = await conn.fetchval("SELECT current_user")
        ground = await admin_conn.fetchrow(
            "SELECT rolsuper FROM pg_roles WHERE rolname = $1", role
        )
        assert ground["rolsuper"] is True, (
            f"{role} (SKYLIZE_TEST_DB_URL) is not a superuser — this refusal "
            "test would prove nothing against it"
        )

        with pytest.raises(ConfigurationError) as excinfo:
            await verify_app_role_is_rls_subject(db)
        message = str(excinfo.value)
        assert repr(role) in message, "the error must name the role"
        assert "has SUPERUSER" in message, "the error must name the attribute"
        assert "silently inert" in message, "the error must state the RLS consequence"
    finally:
        await db.close()


@requires_pg
async def test_bypassrls_only_role_is_refused_naming_bypassrls(admin_conn) -> None:
    """SUPERUSER and BYPASSRLS are distinct disqualifiers; a role with only
    BYPASSRLS must be refused too, and the error must name the right one."""
    role = f"rls_bypass_probe_{uuid.uuid4().hex[:12]}"
    password = uuid.uuid4().hex  # throwaway; the role is dropped below
    await admin_conn.execute(
        f"CREATE ROLE \"{role}\" LOGIN NOSUPERUSER BYPASSRLS PASSWORD '{password}'"
    )
    try:
        db = Database(_dsn_for_role(DB_URL, role, password))
        await db.connect()
        try:
            with pytest.raises(ConfigurationError) as excinfo:
                await verify_app_role_is_rls_subject(db)
            message = str(excinfo.value)
            assert repr(role) in message
            assert "has BYPASSRLS" in message
            assert "has SUPERUSER" not in message, "wrong attribute reported"
        finally:
            await db.close()
    finally:
        await admin_conn.execute(f'DROP ROLE IF EXISTS "{role}"')


# ---------------------------------------------------------------------------
# A down database is a connectivity error, not an RLS finding
# ---------------------------------------------------------------------------

async def test_unreachable_database_is_not_reported_as_a_role_problem() -> None:
    """The check runs only AFTER db.connect() succeeds, and asyncpg errors
    propagate untouched — so an unreachable database can never surface as the
    typed ConfigurationError that means 'your role bypasses RLS'."""
    db = Database("postgresql://nobody:irrelevant@127.0.0.1:9/nowhere")
    with pytest.raises(Exception) as excinfo:
        await asyncio.wait_for(db.connect(), timeout=10)
    assert not isinstance(excinfo.value, ConfigurationError)
    assert isinstance(excinfo.value, (OSError, asyncio.TimeoutError))


# ---------------------------------------------------------------------------
# End to end: the gap the string interlock concedes is now closed
# ---------------------------------------------------------------------------

@requires_pg
@requires_redis
async def test_build_container_refuses_a_respelled_superuser_app_dsn() -> None:
    """A superuser DSN respelled (localhost vs 127.0.0.1) passes the Settings
    raw-string interlock; the composition root must still refuse to build."""
    from skylize.contracts.token import GOVERNANCE_CURVE
    from skylize.security.ecc_service import ECCService

    respelled = _respell(DB_URL)
    assert respelled != DB_URL  # sanity: the string interlock will NOT catch this

    pem = ECCService.generate_key_pair(GOVERNANCE_CURVE).private_pem().decode()
    settings = Settings(
        backend="postgres",
        db_url=DB_URL,
        db_app_url=respelled,
        redis_url=REDIS_URL,
        dev_auth=False,
        jwt_secret=TEST_JWT_SECRET,
        credential_encryption_key=TEST_CREDENTIAL_KEY,
        governance_signing_key_pem=pem,
        anthropic_api_key="",
        llm_demo_mode=True,
    )

    with pytest.raises(ConfigurationError, match="has SUPERUSER"):
        await build_container(settings)
