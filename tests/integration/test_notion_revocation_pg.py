"""Notion's non-expiring grant + live-API revocation against REAL Postgres.

The unit suite proves the wiring with an in-memory repository. This file proves
the parts only a database can:

  * a Notion-shaped grant (`expires_at IS NULL`) actually persists and reads back
    as NULL through the real DAL as the RLS-subject `skylize_app` role;
  * the revocation `connection_state` write is DURABLE and QUERYABLE — the
    in-memory twin cannot catch a transaction that failed to commit, which is the
    same reason `test_oauth_credentials_pg.py` carries its own revocation test;
  * the FULL loop survives a round trip through Postgres: 401 observed -> state
    written -> the next `ensure_fresh` denies;
  * RLS still isolates a non-expiring grant, and one org's revocation does not
    touch another's row.

Skipped unless SKYLIZE_TEST_DB_URL (+ SKYLIZE_TEST_APP_DB_URL) are set.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio

from skylize.app.audit.service import AuditService
from skylize.app.credentials.encryption import FernetEncryptor
from skylize.app.credentials.notion_provider import NOTION_PROVIDER
from skylize.app.credentials.oauth import (
    GrantRevoked,
    GrantStatus,
    OAuthCredentialService,
    evaluate_grant,
)
from skylize.dal.connection import Database
from skylize.dal.memory import InMemoryAuditRepository
from skylize.dal.oauth_credentials import (
    OAuthCredentialRow,
    PgOAuthCredentialRepository,
)
from skylize.events.memory_bus import InMemoryEventBus
from skylize.tools.base import ToolContext, ToolExecutionError
from skylize.tools.builtin.notion_tools import (
    NotionCreatePageIn,
    build_notion_create_page_tool,
)

from .conftest import APP_DB_URL, TEST_CREDENTIAL_KEY, requires_app_role

pytestmark = pytest.mark.integration


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _org() -> str:
    return f"notion_{uuid.uuid4().hex[:8]}"


async def _seed_tenant(admin_conn, org: str) -> None:
    await admin_conn.execute(
        "INSERT INTO tenants (org_id, display_name, oidc_issuer) VALUES ($1,$2,$3) "
        "ON CONFLICT (org_id) DO NOTHING",
        org, org, "https://issuer.example",
    )


async def _cleanup(admin_conn, orgs: list[str]) -> None:
    await admin_conn.execute(
        "DELETE FROM oauth_credentials WHERE org_id = ANY($1::text[])", orgs
    )
    await admin_conn.execute("DELETE FROM tenants WHERE org_id = ANY($1::text[])", orgs)


@pytest_asyncio.fixture()
async def app_db(migrated_public: None):
    if not APP_DB_URL:
        pytest.skip("SKYLIZE_TEST_APP_DB_URL not set")
    db = Database(APP_DB_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


def _row(enc: FernetEncryptor, org: str) -> OAuthCredentialRow:
    """A Notion-shaped grant: NO expiry."""
    now = _now()
    return OAuthCredentialRow(
        cred_id=uuid.uuid4(), org_id=org, provider=NOTION_PROVIDER, label="",
        provider_account_id="workspace-1", key_id="platform-fernet-v1",
        encrypted_access_token=enc.encrypt("notion-access"),
        encrypted_refresh_token=enc.encrypt("notion-refresh"),
        expires_at=None,
        scopes=(), connection_state="valid", state_reason=None,
        created_at=now, updated_at=now, refreshed_at=None,
    )


def _service(repo) -> OAuthCredentialService:
    return OAuthCredentialService(
        encryptor=FernetEncryptor(TEST_CREDENTIAL_KEY),
        repo=repo,
        audit=AuditService(InMemoryEventBus(), InMemoryAuditRepository()),
    )


def _patch_http(monkeypatch, handler) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    real_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(recording)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
    return seen


def _unauthorized(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        401,
        json={
            "object": "error", "status": 401,
            "code": "unauthorized", "message": "API token is invalid.",
        },
    )


# ---------------------------------------------------------------------------
# A Notion-shaped grant persists as non-expiring
# ---------------------------------------------------------------------------

@requires_app_role
async def test_notion_grant_persists_with_a_null_expiry(app_db, admin_conn) -> None:
    org = _org()
    enc = FernetEncryptor(TEST_CREDENTIAL_KEY)
    repo = PgOAuthCredentialRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        await repo.insert(_row(enc, org))

        got = await repo.get(org, NOTION_PROVIDER, "")
        assert got is not None
        assert got.expires_at is None
        assert evaluate_grant(got, now=_now()) is GrantStatus.VALID
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_a_non_expiring_grant_never_refreshes(app_db, admin_conn) -> None:
    """The premise of the whole revocation problem, proven against real storage:
    no clock comparison will ever mark this grant stale, so nothing time-based
    can ever discover that it died."""
    org = _org()
    enc = FernetEncryptor(TEST_CREDENTIAL_KEY)
    repo = PgOAuthCredentialRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        await repo.insert(_row(enc, org))
        svc = _service(repo)

        # No provider registered at all: if a refresh were attempted this would
        # fail closed with RefreshUnavailable. It returns cleanly instead.
        row = await svc.ensure_fresh(org_id=org, provider=NOTION_PROVIDER)
        assert row.expires_at is None
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# THE FULL LOOP, through Postgres
# ---------------------------------------------------------------------------

@requires_app_role
async def test_live_401_writes_durable_revoked_state(
    app_db, admin_conn, monkeypatch
) -> None:
    """The in-memory twin cannot prove this: a state write that never committed
    would still look correct in RAM. Read it back with the ADMIN connection,
    outside the app role's session, so only a committed row can satisfy it."""
    org = _org()
    enc = FernetEncryptor(TEST_CREDENTIAL_KEY)
    repo = PgOAuthCredentialRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        await repo.insert(_row(enc, org))
        svc = _service(repo)
        _patch_http(monkeypatch, _unauthorized)

        ctx = ToolContext(
            org_id=org, agent_id="agency_agent", correlation_id=uuid.uuid4()
        )
        with pytest.raises(ToolExecutionError, match="marked as needing reconnection"):
            await build_notion_create_page_tool(svc).handler(
                NotionCreatePageIn(title="Brief", parent_page_id="p1"), ctx
            )

        state, reason = await admin_conn.fetchrow(
            "SELECT connection_state, state_reason FROM oauth_credentials "
            "WHERE org_id = $1 AND provider = $2",
            org, NOTION_PROVIDER,
        )
        assert state == "revoked", "the revocation did not commit"
        assert "401" in (reason or "")
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_next_gated_call_is_denied_after_a_live_401(
    app_db, admin_conn, monkeypatch
) -> None:
    """End to end: the customer's next Notion tool call refuses at the credential
    gate, which is what actually prompts them to reconnect."""
    org = _org()
    enc = FernetEncryptor(TEST_CREDENTIAL_KEY)
    repo = PgOAuthCredentialRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        await repo.insert(_row(enc, org))
        svc = _service(repo)
        _patch_http(monkeypatch, _unauthorized)

        # Before: the gate passes a healthy non-expiring grant.
        await svc.ensure_fresh(org_id=org, provider=NOTION_PROVIDER)

        ctx = ToolContext(
            org_id=org, agent_id="agency_agent", correlation_id=uuid.uuid4()
        )
        with pytest.raises(ToolExecutionError):
            await build_notion_create_page_tool(svc).handler(
                NotionCreatePageIn(title="Brief", parent_page_id="p1"), ctx
            )

        # After: the same gate the ToolProxy OAuth stage calls now denies.
        with pytest.raises(GrantRevoked):
            await svc.ensure_fresh(org_id=org, provider=NOTION_PROVIDER)
    finally:
        await _cleanup(admin_conn, [org])


@requires_app_role
async def test_403_does_not_revoke_a_real_stored_grant(
    app_db, admin_conn, monkeypatch
) -> None:
    """The negative, against real storage. 403 restricted_resource is our own
    capability misconfiguration or a workspace block limit; the customer's grant
    must be left alone."""
    org = _org()
    enc = FernetEncryptor(TEST_CREDENTIAL_KEY)
    repo = PgOAuthCredentialRepository(app_db)
    try:
        await _seed_tenant(admin_conn, org)
        await repo.insert(_row(enc, org))
        svc = _service(repo)
        _patch_http(
            monkeypatch,
            lambda r: httpx.Response(403, json={
                "object": "error", "status": 403,
                "code": "restricted_resource",
                "message": "Insufficient permissions for this endpoint.",
            }),
        )

        ctx = ToolContext(
            org_id=org, agent_id="agency_agent", correlation_id=uuid.uuid4()
        )
        with pytest.raises(ToolExecutionError, match="403"):
            await build_notion_create_page_tool(svc).handler(
                NotionCreatePageIn(title="Brief", parent_page_id="p1"), ctx
            )

        state = await admin_conn.fetchval(
            "SELECT connection_state FROM oauth_credentials "
            "WHERE org_id = $1 AND provider = $2",
            org, NOTION_PROVIDER,
        )
        assert state == "valid", "a 403 must not be reported as a revocation"
    finally:
        await _cleanup(admin_conn, [org])


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------

@requires_app_role
async def test_rls_isolates_notion_grants(app_db, admin_conn) -> None:
    org_a, org_b = _org(), _org()
    enc = FernetEncryptor(TEST_CREDENTIAL_KEY)
    repo = PgOAuthCredentialRepository(app_db)
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
        await repo.insert(_row(enc, org_a))

        assert await repo.get(org_a, NOTION_PROVIDER, "") is not None
        assert await repo.get(org_b, NOTION_PROVIDER, "") is None, (
            "org B read org A's Notion grant — RLS is not isolating"
        )
    finally:
        await _cleanup(admin_conn, [org_a, org_b])


@requires_app_role
async def test_one_orgs_revocation_does_not_touch_another(
    app_db, admin_conn, monkeypatch
) -> None:
    """Two customers, both connected to Notion; one revokes. The other must be
    completely unaffected — the revocation write is org-scoped, not global."""
    org_a, org_b = _org(), _org()
    enc = FernetEncryptor(TEST_CREDENTIAL_KEY)
    repo = PgOAuthCredentialRepository(app_db)
    try:
        for org in (org_a, org_b):
            await _seed_tenant(admin_conn, org)
            await repo.insert(_row(enc, org))
        svc = _service(repo)
        _patch_http(monkeypatch, _unauthorized)

        ctx = ToolContext(
            org_id=org_a, agent_id="agency_agent", correlation_id=uuid.uuid4()
        )
        with pytest.raises(ToolExecutionError):
            await build_notion_create_page_tool(svc).handler(
                NotionCreatePageIn(title="Brief", parent_page_id="p1"), ctx
            )

        states = {
            r["org_id"]: r["connection_state"]
            for r in await admin_conn.fetch(
                "SELECT org_id, connection_state FROM oauth_credentials "
                "WHERE org_id = ANY($1::text[])",
                [org_a, org_b],
            )
        }
        assert states[org_a] == "revoked"
        assert states[org_b] == "valid", "org B's grant was collaterally revoked"
    finally:
        await _cleanup(admin_conn, [org_a, org_b])
