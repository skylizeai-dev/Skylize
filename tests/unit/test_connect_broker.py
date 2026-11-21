"""Unit tests for the generic OAuth connect broker (OD5).

Every security property listed in the module docstring of
`skylize.app.credentials.connect_broker` has at least one test here. No live
provider is contacted: the token endpoint is an `httpx.MockTransport`.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from skylize.app.credentials.connect_broker import (
    ConnectAccountMismatch,
    ConnectBroker,
    ConnectExchangeFailed,
    ConnectProviderSpec,
    ConnectProviderUnknown,
    ConnectStateInvalid,
    InMemoryConnectStateStore,
    build_code_exchange_request,
    pkce_challenge,
    state_key,
)
from skylize.app.credentials.encryption import FernetEncryptor
from skylize.app.credentials.oauth import OAuthProviderConfig
from skylize.dal.oauth_credentials import InMemoryOAuthCredentialRepository

ORG = "org-connect-1"
OTHER_ORG = "org-connect-2"
USER = "principal-1"
PROVIDER = "acme_docs"  # deliberately not a real provider: the broker is generic
TEST_KEY = "c2t5bGl6ZS1pbnRlZ3JhdGlvbi10ZXN0LWtleSF4MzI="
REDIRECT = "https://app.example.test/oauth/callback"
SECRET = "client-secret-xyz"


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


class _FakeAudit:
    def __init__(self) -> None:
        self.records: list[dict] = []

    async def record(self, **kw):  # noqa: ANN003
        self.records.append(kw)
        return uuid.uuid4()


class _TokenEndpoint:
    """Records every request; answers with a configurable response."""

    def __init__(self, status: int = 200, body: dict | None = None, raise_exc: Exception | None = None) -> None:
        self.status = status
        self.body = body if body is not None else {
            "access_token": "access-new",
            "refresh_token": "refresh-new",
            "expires_in": 3600,
            "scope": "files.read",
            "account_id": "acct-1",
        }
        self.raise_exc = raise_exc
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_exc is not None:
            raise self.raise_exc
        return httpx.Response(self.status, json=self.body)

    def form(self, i: int = 0) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(self.requests[i].content.decode()).items()}


def _spec(**kw) -> ConnectProviderSpec:  # noqa: ANN003
    auth_style = kw.pop("auth_style", "body")
    cfg = OAuthProviderConfig(
        provider=PROVIDER,
        token_url="https://auth.acme.test/token",
        client_id="client-123",
        client_secret=SECRET,
        scopes=("files.read",),
        auth_style=auth_style,
    )
    return ConnectProviderSpec(config=cfg, authorize_url="https://auth.acme.test/authorize", **kw)


def _broker(endpoint: _TokenEndpoint, *, spec: ConnectProviderSpec | None = None, clock: _Clock | None = None):
    store = InMemoryConnectStateStore()
    repo = InMemoryOAuthCredentialRepository()
    audit = _FakeAudit()
    enc = FernetEncryptor(TEST_KEY)
    broker = ConnectBroker(
        providers={PROVIDER: spec or _spec()},
        state_store=store,
        encryptor=enc,
        repo=repo,
        audit=audit,
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(endpoint)),
        now_fn=clock or _Clock(),
    )
    return broker, store, repo, audit, enc


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


# ---------------------------------------------------------------------------
# begin()
# ---------------------------------------------------------------------------

async def test_begin_builds_consent_url_with_state_and_s256_challenge() -> None:
    broker, store, *_ = _broker(_TokenEndpoint())
    redirect = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)

    assert redirect.url.startswith("https://auth.acme.test/authorize?")
    q = _query(redirect.url)
    assert q["response_type"] == "code"
    assert q["client_id"] == "client-123"
    assert q["redirect_uri"] == REDIRECT
    assert q["scope"] == "files.read"
    assert q["state"] == redirect.state
    assert q["code_challenge_method"] == "S256"
    # The challenge is S256 of the verifier the server kept; the verifier itself
    # never appears in the URL.
    pending = await store.take(state_key(redirect.state))
    assert pending is not None and pending.code_verifier is not None
    assert q["code_challenge"] == pkce_challenge(pending.code_verifier)
    assert pending.code_verifier not in redirect.url
    assert SECRET not in redirect.url


async def test_store_is_keyed_by_hash_not_raw_state() -> None:
    broker, store, *_ = _broker(_TokenEndpoint())
    redirect = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    assert await store.take(redirect.state) is None
    assert await store.take(state_key(redirect.state)) is not None


def test_pkce_challenge_matches_rfc7636_appendix_b_vector() -> None:
    assert (
        pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


async def test_begin_unknown_provider_is_refused() -> None:
    broker, store, *_ = _broker(_TokenEndpoint())
    with pytest.raises(ConnectProviderUnknown):
        await broker.begin(org_id=ORG, principal_id=USER, provider="nope", redirect_uri=REDIRECT)
    assert len(store) == 0


@pytest.mark.parametrize(
    "uri",
    ["http://evil.test/cb", "javascript:alert(1)", "/relative/cb", "https://ok.test/cb#frag"],
)
async def test_begin_rejects_unsafe_redirect_uri(uri: str) -> None:
    broker, store, *_ = _broker(_TokenEndpoint())
    with pytest.raises(ValueError):
        await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=uri)
    assert len(store) == 0


async def test_begin_allows_loopback_http_for_local_dev() -> None:
    broker, *_ = _broker(_TokenEndpoint())
    r = await broker.begin(
        org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri="http://localhost:3000/cb"
    )
    assert _query(r.url)["redirect_uri"] == "http://localhost:3000/cb"


def test_spec_cannot_override_broker_owned_params() -> None:
    with pytest.raises(ValueError, match="broker-owned"):
        _spec(extra_authorize_params={"state": "fixed"})
    with pytest.raises(ValueError, match="https"):
        ConnectProviderSpec(config=_spec().config, authorize_url="http://auth.acme.test/authorize")


async def test_extra_authorize_params_are_added() -> None:
    broker, *_ = _broker(_TokenEndpoint(), spec=_spec(extra_authorize_params={"access_type": "offline"}))
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    assert _query(r.url)["access_type"] == "offline"


def test_registry_key_must_match_config_provider() -> None:
    with pytest.raises(ValueError, match="does not match"):
        ConnectBroker(
            providers={"other": _spec()},
            state_store=InMemoryConnectStateStore(),
            encryptor=FernetEncryptor(TEST_KEY),
            repo=InMemoryOAuthCredentialRepository(),
            audit=_FakeAudit(),  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# complete() — happy paths
# ---------------------------------------------------------------------------

async def test_complete_exchanges_code_and_stores_encrypted_grant() -> None:
    endpoint = _TokenEndpoint()
    broker, store, repo, audit, enc = _broker(endpoint)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)

    row = await broker.complete(state=r.state, code="auth-code-1", org_id=ORG, principal_id=USER)

    form = endpoint.form()
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "auth-code-1"
    assert form["redirect_uri"] == REDIRECT  # from the stored record
    assert pkce_challenge(form["code_verifier"]) == _query(r.url)["code_challenge"]
    assert form["client_id"] == "client-123" and form["client_secret"] == SECRET
    assert "authorization" not in endpoint.requests[0].headers

    assert row.org_id == ORG and row.provider == PROVIDER and row.label == ""
    assert row.provider_account_id == "acct-1"
    assert row.connection_state == "valid"
    assert row.encrypted_access_token != "access-new"
    assert enc.decrypt(row.encrypted_access_token) == "access-new"
    assert row.encrypted_refresh_token is not None
    assert enc.decrypt(row.encrypted_refresh_token) == "refresh-new"
    assert row.expires_at == datetime(2026, 11, 14, 13, 0, tzinfo=timezone.utc)
    assert await repo.get(ORG, PROVIDER, "") == row
    assert len(store) == 0
    assert [a["action_type"] for a in audit.records] == ["oauth.connect_started", "oauth.connect_completed"]


async def test_audit_trail_never_contains_secrets() -> None:
    endpoint = _TokenEndpoint()
    broker, store, repo, audit, _ = _broker(endpoint)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    await broker.complete(state=r.state, code="auth-code-1", org_id=ORG, principal_id=USER)
    dumped = json.dumps(audit.records, default=str)
    verifier = endpoint.form()["code_verifier"]
    for secret in (r.state, "auth-code-1", "access-new", "refresh-new", SECRET, verifier):
        assert secret not in dumped


async def test_header_auth_style_uses_basic_and_keeps_secret_out_of_body() -> None:
    endpoint = _TokenEndpoint()
    broker, *_ = _broker(endpoint, spec=_spec(auth_style="header"))
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    await broker.complete(state=r.state, code="c", org_id=ORG, principal_id=USER)
    form = endpoint.form()
    assert "client_secret" not in form and "client_id" not in form
    expected = base64.b64encode(f"client-123:{SECRET}".encode()).decode()
    assert endpoint.requests[0].headers["authorization"] == f"Basic {expected}"


async def test_pkce_off_sends_no_challenge_and_no_verifier() -> None:
    endpoint = _TokenEndpoint()
    broker, *_ = _broker(endpoint, spec=_spec(use_pkce=False))
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    q = _query(r.url)
    assert "code_challenge" not in q and "code_challenge_method" not in q
    await broker.complete(state=r.state, code="c", org_id=ORG, principal_id=USER)
    assert "code_verifier" not in endpoint.form()


async def test_non_expiring_grant_stores_null_expiry() -> None:
    endpoint = _TokenEndpoint(body={"access_token": "a", "expires_in": 60})
    spec = _spec()
    from dataclasses import replace

    from skylize.app.credentials.oauth import TokenResponse

    cfg = replace(
        spec.config,
        parse_token_response=lambda p: TokenResponse(
            access_token=p["access_token"], expires_in_seconds=None, refresh_token=None
        ),
    )
    broker, *_ = _broker(endpoint, spec=ConnectProviderSpec(config=cfg, authorize_url=spec.authorize_url))
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    row = await broker.complete(state=r.state, code="c", org_id=ORG, principal_id=USER)
    assert row.expires_at is None and row.encrypted_refresh_token is None
    assert row.scopes == ("files.read",)  # requested scopes when the response names none


# ---------------------------------------------------------------------------
# complete() — refusals (nothing stored, no replay)
# ---------------------------------------------------------------------------

async def test_state_is_single_use() -> None:
    endpoint = _TokenEndpoint()
    broker, *_ = _broker(endpoint)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    await broker.complete(state=r.state, code="c", org_id=ORG, principal_id=USER)
    with pytest.raises(ConnectStateInvalid):
        await broker.complete(state=r.state, code="c", org_id=ORG, principal_id=USER)
    assert len(endpoint.requests) == 1


async def test_unknown_or_tampered_state_is_refused_without_exchange() -> None:
    endpoint = _TokenEndpoint()
    broker, _, repo, audit, _ = _broker(endpoint)
    await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    for bad in ("forged-state", ""):
        with pytest.raises(ConnectStateInvalid):
            await broker.complete(state=bad, code="c", org_id=ORG, principal_id=USER)
    assert endpoint.requests == []
    assert await repo.list_for_org(ORG) == []
    assert audit.records[-1]["action_type"] == "oauth.connect_denied"


async def test_expired_state_is_refused_without_exchange() -> None:
    endpoint = _TokenEndpoint()
    clock = _Clock()
    broker, store, repo, *_ = _broker(endpoint, clock=clock)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    clock.now += timedelta(minutes=10)
    with pytest.raises(ConnectStateInvalid):
        await broker.complete(state=r.state, code="c", org_id=ORG, principal_id=USER)
    assert endpoint.requests == [] and len(store) == 0
    assert await repo.list_for_org(ORG) == []


@pytest.mark.parametrize("org,user", [(OTHER_ORG, USER), (ORG, "principal-2")])
async def test_state_bound_to_starting_session(org: str, user: str) -> None:
    endpoint = _TokenEndpoint()
    broker, store, repo, audit, _ = _broker(endpoint)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    with pytest.raises(ConnectStateInvalid):
        await broker.complete(state=r.state, code="c", org_id=org, principal_id=user)
    assert endpoint.requests == []
    assert await repo.list_for_org(ORG) == [] and await repo.list_for_org(OTHER_ORG) == []
    # Consumed: even the rightful session cannot now replay it.
    with pytest.raises(ConnectStateInvalid):
        await broker.complete(state=r.state, code="c", org_id=ORG, principal_id=USER)
    denied = [a for a in audit.records if a["action_type"] == "oauth.connect_denied"]
    assert denied[0]["org_id"] == ORG  # audited under the org whose flow it was


async def test_provider_error_stores_nothing_and_echoes_no_body() -> None:
    endpoint = _TokenEndpoint(status=400, body={"error": "invalid_grant", "error_description": "leaky detail"})
    broker, _, repo, *_ = _broker(endpoint)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    with pytest.raises(ConnectExchangeFailed) as exc_info:
        await broker.complete(state=r.state, code="c", org_id=ORG, principal_id=USER)
    assert "invalid_grant" in str(exc_info.value)
    assert "leaky detail" not in str(exc_info.value)
    assert await repo.list_for_org(ORG) == []


@pytest.mark.parametrize(
    "exc",
    [httpx.ReadTimeout("t"), httpx.RemoteProtocolError("dropped"), httpx.ConnectError("refused")],
)
async def test_transport_error_is_attempted_once_and_reports_outcome_unknown(exc: Exception) -> None:
    endpoint = _TokenEndpoint(raise_exc=exc)
    broker, _, repo, *_ = _broker(endpoint)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    with pytest.raises(ConnectExchangeFailed, match="OUTCOME UNKNOWN"):
        await broker.complete(state=r.state, code="c", org_id=ORG, principal_id=USER)
    assert len(endpoint.requests) == 1
    assert await repo.list_for_org(ORG) == []


async def test_malformed_token_response_stores_nothing() -> None:
    endpoint = _TokenEndpoint(body={"token_type": "bearer"})
    broker, _, repo, *_ = _broker(endpoint)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    with pytest.raises(ConnectExchangeFailed, match="unusable token response"):
        await broker.complete(state=r.state, code="c", org_id=ORG, principal_id=USER)
    assert await repo.list_for_org(ORG) == []


# ---------------------------------------------------------------------------
# Reconnect
# ---------------------------------------------------------------------------

async def test_reconnect_same_account_revives_revoked_grant_in_place() -> None:
    endpoint = _TokenEndpoint()
    broker, _, repo, audit, enc = _broker(endpoint)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    first = await broker.complete(state=r.state, code="c1", org_id=ORG, principal_id=USER)
    await repo.set_connection_state(cred_id=first.cred_id, org_id=ORG, state="revoked", reason="x")

    endpoint.body = {**endpoint.body, "access_token": "access-2"}
    r2 = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    second = await broker.complete(state=r2.state, code="c2", org_id=ORG, principal_id=USER)

    assert second.cred_id == first.cred_id
    assert second.connection_state == "valid"
    assert enc.decrypt(second.encrypted_access_token) == "access-2"
    assert len(await repo.list_for_org(ORG)) == 1
    assert audit.records[-1]["action_type"] == "oauth.connect_reconnected"


async def test_reconnect_with_different_account_is_refused_and_row_untouched() -> None:
    endpoint = _TokenEndpoint()
    broker, _, repo, *_ = _broker(endpoint)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    first = await broker.complete(state=r.state, code="c1", org_id=ORG, principal_id=USER)

    endpoint.body = {**endpoint.body, "access_token": "attacker", "account_id": "acct-OTHER"}
    r2 = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    with pytest.raises(ConnectAccountMismatch):
        await broker.complete(state=r2.state, code="c2", org_id=ORG, principal_id=USER)
    assert await repo.get(ORG, PROVIDER, "") == first


async def test_labels_are_independent_connections() -> None:
    endpoint = _TokenEndpoint()
    broker, _, repo, *_ = _broker(endpoint)
    for label in ("", "marketing"):
        r = await broker.begin(
            org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT, label=label
        )
        await broker.complete(state=r.state, code="c", org_id=ORG, principal_id=USER)
    assert [row.label for row in await repo.list_for_org(ORG)] == ["", "marketing"]


# ---------------------------------------------------------------------------
# Pure request builder
# ---------------------------------------------------------------------------

def test_extra_token_params_cannot_override_protocol_fields() -> None:
    from dataclasses import replace

    cfg = replace(_spec().config, extra_token_params={"grant_type": "password", "audience": "api"})
    data, _ = build_code_exchange_request(cfg, code="c", redirect_uri=REDIRECT, code_verifier=None)
    assert data["grant_type"] == "authorization_code"
    assert data["audience"] == "api"


# ---------------------------------------------------------------------------
# Independent-auditor findings (audit-D3 F1-F8) and pin gaps (a3, a4, g3, i2, i3)
# ---------------------------------------------------------------------------

def _no_account(body: dict) -> dict:
    return {k: v for k, v in body.items() if k != "account_id"}


async def test_reconnect_with_unnamed_account_fails_closed() -> None:
    """F1: an empty name on both sides is not proof of the same upstream account."""
    endpoint = _TokenEndpoint()
    endpoint.body = _no_account(endpoint.body)
    broker, _, repo, *_ = _broker(endpoint)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    first = await broker.complete(state=r.state, code="c1", org_id=ORG, principal_id=USER)
    assert first.provider_account_id == ""
    endpoint.body = {**endpoint.body, "access_token": "someone-else"}
    r2 = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    with pytest.raises(ConnectAccountMismatch):
        await broker.complete(state=r2.state, code="c2", org_id=ORG, principal_id=USER)
    assert await repo.get(ORG, PROVIDER, "") == first


async def test_numeric_account_id_is_a_name() -> None:
    endpoint = _TokenEndpoint()
    endpoint.body = {**_no_account(endpoint.body), "workspace_id": True, "user_id": 4242}
    broker, *_ = _broker(endpoint)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    row = await broker.complete(state=r.state, code="c", org_id=ORG, principal_id=USER)
    assert row.provider_account_id == "4242"


async def test_reconnect_without_refresh_token_keeps_stored_one() -> None:
    """F2: same rule as the refresh path; never erase the stored refresh token."""
    endpoint = _TokenEndpoint()
    broker, _, _, _, enc = _broker(endpoint)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    await broker.complete(state=r.state, code="c1", org_id=ORG, principal_id=USER)
    endpoint.body = {k: v for k, v in endpoint.body.items() if k != "refresh_token"}
    r2 = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    row = await broker.complete(state=r2.state, code="c2", org_id=ORG, principal_id=USER)
    assert row.encrypted_refresh_token is not None
    assert enc.decrypt(row.encrypted_refresh_token) == "refresh-new"


async def test_reconnect_audit_records_granted_scopes() -> None:
    """F3: the stored scopes column is not rewritten on reconnect; the audit
    records what this exchange granted next to the stored set."""
    endpoint = _TokenEndpoint()
    broker, _, _, audit, _ = _broker(endpoint)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    await broker.complete(state=r.state, code="c1", org_id=ORG, principal_id=USER)
    endpoint.body = {**endpoint.body, "scope": "files.read files.write"}
    r2 = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    await broker.complete(state=r2.state, code="c2", org_id=ORG, principal_id=USER)
    last = audit.records[-1]["inputs"]
    assert last["scopes_granted"] == ["files.read", "files.write"]
    assert last["scopes_stored"] == ["files.read"]


async def test_foreign_session_with_expired_state_is_audited_under_starting_org() -> None:
    """F4: binding is checked before expiry."""
    endpoint = _TokenEndpoint()
    clock = _Clock()
    broker, _, _, audit, _ = _broker(endpoint, clock=clock)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    clock.now += timedelta(hours=1)
    with pytest.raises(ConnectStateInvalid):
        await broker.complete(state=r.state, code="c", org_id=OTHER_ORG, principal_id="intruder")
    denied = audit.records[-1]
    assert denied["org_id"] == ORG
    assert denied["result_reason"] == "state bound to a different session"
    assert not any(a["org_id"] == OTHER_ORG for a in audit.records)


@pytest.mark.parametrize(
    "status,body,exc,kind",
    [
        (400, {"error": "invalid_grant"}, None, "provider_rejected"),
        (200, None, httpx.ReadTimeout("t"), "outcome_unknown"),
        (200, {"token_type": "bearer"}, None, "unusable_response"),
    ],
)
async def test_exchange_failures_are_audited_and_state_stays_spent(
    status: int, body: dict | None, exc: Exception | None, kind: str
) -> None:
    """F5 + pin gap a3."""
    endpoint = _TokenEndpoint(status=status, body=body, raise_exc=exc)
    broker, store, _, audit, _ = _broker(endpoint)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    with pytest.raises(ConnectExchangeFailed) as exc_info:
        await broker.complete(state=r.state, code="code-SECRET", org_id=ORG, principal_id=USER)
    assert exc_info.value.kind == kind
    assert audit.records[-1]["action_type"] == "oauth.connect_denied"
    assert audit.records[-1]["result_reason"] == f"code exchange failed ({kind})"
    assert len(store) == 0
    with pytest.raises(ConnectStateInvalid):
        await broker.complete(state=r.state, code="code-SECRET", org_id=ORG, principal_id=USER)


async def test_missing_code_spends_state() -> None:
    """Pin gap a4."""
    endpoint = _TokenEndpoint()
    broker, store, *_ = _broker(endpoint)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    with pytest.raises(ConnectExchangeFailed) as exc_info:
        await broker.complete(state=r.state, code="", org_id=ORG, principal_id=USER)
    assert exc_info.value.kind == "missing_code"
    assert len(store) == 0 and endpoint.requests == []


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_5xx_exchange_is_not_retried(status: int) -> None:
    """Pin gap g3."""
    endpoint = _TokenEndpoint(status=status, body={"error": "temporarily_unavailable"})
    broker, *_ = _broker(endpoint)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    with pytest.raises(ConnectExchangeFailed):
        await broker.complete(state=r.state, code="c", org_id=ORG, principal_id=USER)
    assert len(endpoint.requests) == 1


async def test_deny_paths_never_audit_state_or_code() -> None:
    """Pin gaps i2/i3."""
    endpoint = _TokenEndpoint(status=400, body={"error": "invalid_grant"})
    broker, _, _, audit, _ = _broker(endpoint)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    attempts = (
        {"state": "forged-STATE", "code": "code-ONE"},
        {"state": r.state, "code": "code-TWO"},
    )
    for attempt in attempts:
        with pytest.raises((ConnectStateInvalid, ConnectExchangeFailed)):
            await broker.complete(org_id=ORG, principal_id=USER, **attempt)
    dumped = json.dumps(audit.records, default=str)
    for secret in ("forged-STATE", r.state, "code-ONE", "code-TWO"):
        assert secret not in dumped


async def test_provider_error_code_is_sanitised() -> None:
    """F6: a provider-controlled `error` is echoed only when it is a plain token."""
    endpoint = _TokenEndpoint(status=400, body={"error": "x\nFAKE LOG LINE level=critical"})
    broker, *_ = _broker(endpoint)
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    with pytest.raises(ConnectExchangeFailed) as exc_info:
        await broker.complete(state=r.state, code="c", org_id=ORG, principal_id=USER)
    assert "FAKE LOG LINE" not in str(exc_info.value)
    assert "error=unrecognized" in str(exc_info.value)


def test_token_url_must_be_https() -> None:
    """F7."""
    from dataclasses import replace

    with pytest.raises(ValueError, match="token_url"):
        ConnectProviderSpec(
            config=replace(_spec().config, token_url="http://auth.acme.test/token"),
            authorize_url="https://auth.acme.test/authorize",
        )


async def test_concurrent_first_connect_reports_the_race() -> None:
    """F8: the loser of two first-time connects gets a typed error, not a driver error."""
    from dataclasses import replace

    class _RacingRepo(InMemoryOAuthCredentialRepository):
        async def insert(self, row):  # type: ignore[no-untyped-def]
            await super().insert(replace(row, cred_id=uuid.uuid4()))  # the rival lands first
            raise RuntimeError("duplicate key value violates unique constraint")

    broker, _, _, audit, _ = _broker(_TokenEndpoint())
    broker._repo = _RacingRepo()  # type: ignore[attr-defined]
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    with pytest.raises(ConnectExchangeFailed) as exc_info:
        await broker.complete(state=r.state, code="c", org_id=ORG, principal_id=USER)
    assert exc_info.value.kind == "concurrent_connect"
    assert audit.records[-1]["result_reason"] == "concurrent connect already stored a grant"


async def test_insert_failure_without_a_rival_row_propagates() -> None:
    class _BrokenRepo(InMemoryOAuthCredentialRepository):
        async def insert(self, row):  # type: ignore[no-untyped-def]
            raise RuntimeError("db down")

    broker, *_ = _broker(_TokenEndpoint())
    broker._repo = _BrokenRepo()  # type: ignore[attr-defined]
    r = await broker.begin(org_id=ORG, principal_id=USER, provider=PROVIDER, redirect_uri=REDIRECT)
    with pytest.raises(RuntimeError, match="db down"):
        await broker.complete(state=r.state, code="c", org_id=ORG, principal_id=USER)
