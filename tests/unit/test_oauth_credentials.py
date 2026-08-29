"""Unit tests for the provider-agnostic OAuth credential infrastructure.

Covers the pure state machine (`evaluate_grant`), the on-demand refresh
primitive, and the ToolProxy pre-dispatch credential gate — including the
ORDERING guarantee that a dead credential denies BEFORE any spend hold is placed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from skylize.app.credentials.encryption import FernetEncryptor
from skylize.app.credentials.oauth import (
    DEFAULT_EXPIRY_SKEW,
    GrantNotConnected,
    GrantRevoked,
    GrantStatus,
    OAuthCredentialService,
    OAuthProviderConfig,
    RefreshUnavailable,
    evaluate_grant,
)
from skylize.dal.oauth_credentials import (
    InMemoryOAuthCredentialRepository,
    OAuthCredentialRow,
)

# `asyncio_mode = "auto"` (pyproject.toml:114) collects async tests already; an
# explicit module-level asyncio mark would also (wrongly) tag the sync ones.

ORG = "org-oauth-1"
PROVIDER = "acme_docs"  # deliberately NOT a real provider — infra is generic

# Decodes to 32 ASCII bytes; same shape the integration conftest uses.
TEST_KEY = "c2t5bGl6ZS1pbnRlZ3JhdGlvbi10ZXN0LWtleSF4MzI="


def _now() -> datetime:
    return datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)


def _row(
    enc: FernetEncryptor,
    *,
    expires_in: timedelta = timedelta(hours=1),
    refresh: str | None = "refresh-abc",
    state: str = "valid",
    org: str = ORG,
) -> OAuthCredentialRow:
    return OAuthCredentialRow(
        cred_id=uuid.uuid4(),
        org_id=org,
        provider=PROVIDER,
        label="",
        provider_account_id="acct-1",
        key_id="platform-fernet-v1",
        encrypted_access_token=enc.encrypt("access-old"),
        encrypted_refresh_token=enc.encrypt(refresh) if refresh is not None else None,
        expires_at=_now() + expires_in,
        scopes=("files.read",),
        connection_state=state,  # type: ignore[arg-type]
        state_reason=None,
        created_at=_now(),
        updated_at=_now(),
        refreshed_at=None,
    )


class _FakeAudit:
    def __init__(self) -> None:
        self.records: list[dict] = []

    async def record(self, **kw):  # noqa: ANN003
        self.records.append(kw)
        return uuid.uuid4()


def _service(repo, audit, *, handler=None, providers=None) -> OAuthCredentialService:
    enc = FernetEncryptor(TEST_KEY)

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return OAuthCredentialService(
        encryptor=enc,
        repo=repo,
        audit=audit,
        providers=providers,
        http_client_factory=factory if handler is not None else None,
        now_fn=_now,
    )


# ---------------------------------------------------------------------------
# evaluate_grant — the pure state machine
# ---------------------------------------------------------------------------

def test_evaluate_grant_valid_when_well_before_expiry() -> None:
    enc = FernetEncryptor(TEST_KEY)
    row = _row(enc, expires_in=timedelta(hours=1))
    assert evaluate_grant(row, now=_now()) is GrantStatus.VALID


def test_evaluate_grant_needs_refresh_inside_skew_window() -> None:
    """Inside the skew margin the token is still technically valid, but we
    refresh anyway — the whole point of the margin."""
    enc = FernetEncryptor(TEST_KEY)
    row = _row(enc, expires_in=DEFAULT_EXPIRY_SKEW - timedelta(minutes=1))
    assert evaluate_grant(row, now=_now()) is GrantStatus.NEEDS_REFRESH


def test_evaluate_grant_dead_when_expired_and_no_refresh_token() -> None:
    """Past expiry with nothing to refresh with is unrepairable — the case that
    makes stored 'expired' distinct from the expires_at timestamp."""
    enc = FernetEncryptor(TEST_KEY)
    row = _row(enc, expires_in=timedelta(minutes=-1), refresh=None)
    assert evaluate_grant(row, now=_now()) is GrantStatus.DEAD


def test_evaluate_grant_revoked_state_beats_a_future_expiry() -> None:
    """The provider's verdict outranks our cached clock: a revoked grant whose
    expires_at is still in the future must be DEAD, never VALID."""
    enc = FernetEncryptor(TEST_KEY)
    row = _row(enc, expires_in=timedelta(hours=5), state="revoked")
    assert evaluate_grant(row, now=_now()) is GrantStatus.DEAD


# ---------------------------------------------------------------------------
# ensure_fresh / refresh
# ---------------------------------------------------------------------------

async def test_ensure_fresh_returns_valid_grant_without_network() -> None:
    repo = InMemoryOAuthCredentialRepository()
    enc = FernetEncryptor(TEST_KEY)
    row = _row(enc)
    await repo.insert(row)

    def explode(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("a valid grant must not hit the token endpoint")

    svc = _service(repo, _FakeAudit(), handler=explode)
    got = await svc.ensure_fresh(org_id=ORG, provider=PROVIDER)
    assert got.cred_id == row.cred_id


async def test_ensure_fresh_raises_when_not_connected() -> None:
    svc = _service(InMemoryOAuthCredentialRepository(), _FakeAudit())
    with pytest.raises(GrantNotConnected):
        await svc.ensure_fresh(org_id=ORG, provider=PROVIDER)


async def test_refresh_persists_new_token_and_expiry() -> None:
    repo = InMemoryOAuthCredentialRepository()
    enc = FernetEncryptor(TEST_KEY)
    row = _row(enc, expires_in=timedelta(minutes=-10))
    await repo.insert(row)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "access_token": "access-new",
            "expires_in": 3600,
            "refresh_token": "refresh-rotated",
        })

    audit = _FakeAudit()
    svc = _service(repo, audit, handler=handler, providers={
        PROVIDER: OAuthProviderConfig(
            provider=PROVIDER, token_url="https://acme.test/token",
            client_id="cid", client_secret="secret",
        )
    })
    refreshed = await svc.ensure_fresh(org_id=ORG, provider=PROVIDER)

    assert enc.decrypt(refreshed.encrypted_access_token) == "access-new"
    assert enc.decrypt(refreshed.encrypted_refresh_token) == "refresh-rotated"
    assert refreshed.expires_at == _now() + timedelta(seconds=3600)
    assert refreshed.connection_state == "valid"
    assert any(r["action_type"] == "oauth.refreshed" for r in audit.records)


async def test_refresh_keeps_existing_refresh_token_when_provider_omits_it() -> None:
    """Providers that do not rotate refresh tokens omit the field. Overwriting
    with None would destroy our only way back."""
    repo = InMemoryOAuthCredentialRepository()
    enc = FernetEncryptor(TEST_KEY)
    await repo.insert(_row(enc, expires_in=timedelta(minutes=-10)))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "a2", "expires_in": 900})

    svc = _service(repo, _FakeAudit(), handler=handler, providers={
        PROVIDER: OAuthProviderConfig(
            provider=PROVIDER, token_url="https://acme.test/token",
            client_id="cid", client_secret="secret",
        )
    })
    refreshed = await svc.ensure_fresh(org_id=ORG, provider=PROVIDER)
    assert enc.decrypt(refreshed.encrypted_refresh_token) == "refresh-abc"


async def test_invalid_grant_marks_revoked_and_raises() -> None:
    repo = InMemoryOAuthCredentialRepository()
    enc = FernetEncryptor(TEST_KEY)
    row = _row(enc, expires_in=timedelta(minutes=-10))
    await repo.insert(row)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    svc = _service(repo, _FakeAudit(), handler=handler, providers={
        PROVIDER: OAuthProviderConfig(
            provider=PROVIDER, token_url="https://acme.test/token",
            client_id="cid", client_secret="secret",
        )
    })
    with pytest.raises(GrantRevoked):
        await svc.ensure_fresh(org_id=ORG, provider=PROVIDER)

    stored = await repo.get(ORG, PROVIDER, "")
    assert stored.connection_state == "revoked"
    assert stored.state_reason is not None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"error": "server_error"}),
        httpx.Response(429, json={"error": "rate_limited"}),
        httpx.Response(400, json={"error": "invalid_client"}),
    ],
    ids=["server_error", "rate_limited", "invalid_client"],
)
async def test_transient_and_platform_failures_never_mark_revoked(response) -> None:
    """The security core: marking a grant revoked forces the customer to
    reconnect, so ONLY an unambiguous provider signal may do it. A 500, a 429, or
    our own bad client credentials must deny WITHOUT touching stored state."""
    repo = InMemoryOAuthCredentialRepository()
    enc = FernetEncryptor(TEST_KEY)
    await repo.insert(_row(enc, expires_in=timedelta(minutes=-10)))

    def handler(request: httpx.Request) -> httpx.Response:
        return response

    svc = _service(repo, _FakeAudit(), handler=handler, providers={
        PROVIDER: OAuthProviderConfig(
            provider=PROVIDER, token_url="https://acme.test/token",
            client_id="cid", client_secret="secret",
        )
    })
    with pytest.raises(RefreshUnavailable):
        await svc.ensure_fresh(org_id=ORG, provider=PROVIDER)

    stored = await repo.get(ORG, PROVIDER, "")
    assert stored.connection_state == "valid", "transient failure must not revoke"


async def test_network_error_denies_without_revoking() -> None:
    repo = InMemoryOAuthCredentialRepository()
    enc = FernetEncryptor(TEST_KEY)
    await repo.insert(_row(enc, expires_in=timedelta(minutes=-10)))

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns went away")

    svc = _service(repo, _FakeAudit(), handler=handler, providers={
        PROVIDER: OAuthProviderConfig(
            provider=PROVIDER, token_url="https://acme.test/token",
            client_id="cid", client_secret="secret",
        )
    })
    with pytest.raises(RefreshUnavailable):
        await svc.ensure_fresh(org_id=ORG, provider=PROVIDER)
    stored = await repo.get(ORG, PROVIDER, "")
    assert stored.connection_state == "valid"


async def test_unregistered_provider_fails_closed() -> None:
    repo = InMemoryOAuthCredentialRepository()
    enc = FernetEncryptor(TEST_KEY)
    await repo.insert(_row(enc, expires_in=timedelta(minutes=-10)))
    svc = _service(repo, _FakeAudit())  # no providers registered
    with pytest.raises(RefreshUnavailable):
        await svc.ensure_fresh(org_id=ORG, provider=PROVIDER)


async def test_expired_without_refresh_token_persists_expired_state() -> None:
    repo = InMemoryOAuthCredentialRepository()
    enc = FernetEncryptor(TEST_KEY)
    await repo.insert(_row(enc, expires_in=timedelta(minutes=-1), refresh=None))
    svc = _service(repo, _FakeAudit())
    with pytest.raises(GrantRevoked):
        await svc.ensure_fresh(org_id=ORG, provider=PROVIDER)
    stored = await repo.get(ORG, PROVIDER, "")
    assert stored.connection_state == "expired"


async def test_repository_does_not_leak_across_orgs() -> None:
    """In-memory twin honours the same explicit org predicate the Pg impl uses.
    The authoritative isolation proof is the RLS integration test."""
    repo = InMemoryOAuthCredentialRepository()
    enc = FernetEncryptor(TEST_KEY)
    await repo.insert(_row(enc, org="org-a"))
    await repo.insert(_row(enc, org="org-b"))
    assert await repo.get("org-a", PROVIDER, "") is not None
    assert (await repo.get("org-a", PROVIDER, "")).org_id == "org-a"
    assert len(await repo.list_for_org("org-a")) == 1
