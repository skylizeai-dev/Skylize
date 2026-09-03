"""The two OAuth-primitive extensions that unblock Notion (header auth, nullable
expiry), and the regression guarantees that make them safe for Drive and Asana.

This suite exists because the change touches a primitive TWO shipped connectors
already depend on. It is organised around the three things that could go wrong:

  1. `auth_style='body'` stops producing byte-identical requests -> Drive and
     Asana silently break at a real token endpoint. Pinned by
     `TestBodyModeIsUnchanged`.
  2. A NULL `expires_at` is mishandled somewhere that assumed a real timestamp ->
     a non-expiring grant is treated as expired (locks the customer out) or a
     terminal state is ignored (uses a dead grant). Pinned by
     `TestNullableExpiry`.
  3. The revocation gap the nullable change opens is left unclosed -> a revoked
     non-expiring grant stays 'valid' forever. Pinned by
     `TestLiveApiRevocation`.

`PROVIDER` is deliberately synthetic throughout. This pass extends the
infrastructure only; no Notion connector is built, so the new hooks are proven
against a mock provider config rather than a real Notion integration.
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from skylize.app.credentials.oauth import (
    DEFAULT_EXPIRY_SKEW,
    GrantStatus,
    OAuthCredentialService,
    OAuthProviderConfig,
    RefreshUnavailable,
    TokenResponse,
    _build_token_request,
    _default_parse_token_response,
    evaluate_grant,
)
from skylize.app.credentials.encryption import FernetEncryptor
from skylize.dal.oauth_credentials import (
    InMemoryOAuthCredentialRepository,
    OAuthCredentialRow,
)

ORG = "org-oauth-ext"
PROVIDER = "synthetic_provider"  # NOT a real provider — infra is generic
TEST_KEY = "c2t5bGl6ZS1pbnRlZ3JhdGlvbi10ZXN0LWtleSF4MzI="


def _now() -> datetime:
    return datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)


def _config(**over) -> OAuthProviderConfig:
    base = dict(
        provider=PROVIDER,
        token_url="https://provider.test/oauth/token",
        client_id="the-client-id",
        client_secret="the-client-secret",
    )
    base.update(over)
    return OAuthProviderConfig(**base)  # type: ignore[arg-type]


def _row(
    enc: FernetEncryptor,
    *,
    expires_at: datetime | None,
    refresh: str | None = "refresh-abc",
    state: str = "valid",
) -> OAuthCredentialRow:
    return OAuthCredentialRow(
        cred_id=uuid.uuid4(),
        org_id=ORG,
        provider=PROVIDER,
        label="",
        provider_account_id="acct-1",
        key_id="platform-fernet-v1",
        encrypted_access_token=enc.encrypt("access-old"),
        encrypted_refresh_token=enc.encrypt(refresh) if refresh is not None else None,
        expires_at=expires_at,
        scopes=(),
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
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return OAuthCredentialService(
        encryptor=FernetEncryptor(TEST_KEY),
        repo=repo,
        audit=audit,
        providers=providers,
        http_client_factory=factory if handler is not None else None,
        now_fn=_now,
    )


# ---------------------------------------------------------------------------
# 1. REGRESSION: body mode must be byte-identical to pre-change behaviour
# ---------------------------------------------------------------------------

class TestBodyModeIsUnchanged:
    """Drive and Asana depend on this. If any assertion here fails, the change
    is NOT additive and must not merge."""

    def test_body_is_the_default(self) -> None:
        """A config that never mentions auth_style behaves exactly as before."""
        assert _config().auth_style == "body"

    def test_body_mode_sends_credentials_in_the_form_body(self) -> None:
        data, headers = _build_token_request(_config(), "rt-123")
        assert data == {
            "grant_type": "refresh_token",
            "refresh_token": "rt-123",
            "client_id": "the-client-id",
            "client_secret": "the-client-secret",
        }
        assert headers == {}, "body mode must add no headers at all"

    def test_body_mode_key_order_is_unchanged(self) -> None:
        """Form encoding preserves insertion order, so the encoded body is
        byte-identical to what the pre-change dict literal produced."""
        data, _ = _build_token_request(_config(), "rt-123")
        assert list(data) == [
            "grant_type", "refresh_token", "client_id", "client_secret"
        ]

    def test_extra_token_params_still_apply_last_and_can_override(self) -> None:
        """Pre-change, extras were splatted last into the dict literal and could
        override any body field. That semantic is preserved exactly."""
        data, _ = _build_token_request(
            _config(extra_token_params={"client_id": "overridden", "audience": "x"}),
            "rt-123",
        )
        assert data["client_id"] == "overridden"
        assert data["audience"] == "x"

    def test_drive_config_is_body_mode(self) -> None:
        from skylize.app.credentials.google_provider import (
            build_google_drive_provider_config,
        )

        cfg = build_google_drive_provider_config(client_id="c", client_secret="s")
        assert cfg.auth_style == "body"
        data, headers = _build_token_request(cfg, "rt")
        assert data["client_id"] == "c" and data["client_secret"] == "s"
        assert headers == {}

    def test_asana_config_is_body_mode(self) -> None:
        from skylize.app.credentials.asana_provider import build_asana_provider_config

        cfg = build_asana_provider_config(client_id="c", client_secret="s")
        assert cfg.auth_style == "body"
        data, headers = _build_token_request(cfg, "rt")
        assert data["client_id"] == "c" and data["client_secret"] == "s"
        assert headers == {}

    def test_default_parser_still_requires_expires_in(self) -> None:
        """The strictness that protects the EXISTING providers must not relax.

        If the default parser started tolerating a missing `expires_in`, a
        malformed Drive/Asana response would silently become a permanent
        non-expiring credential instead of an error. Nullable expiry is opt-in
        via a provider-specific parser, never a default.
        """
        with pytest.raises(RefreshUnavailable, match="non-numeric expires_in"):
            _default_parse_token_response({"access_token": "at"})

    def test_default_parser_still_produces_an_int(self) -> None:
        token = _default_parse_token_response(
            {"access_token": "at", "expires_in": 3600}
        )
        assert token.expires_in_seconds == 3600


# ---------------------------------------------------------------------------
# 2. NEW: header auth
# ---------------------------------------------------------------------------

class TestHeaderAuth:
    def test_header_mode_sends_basic_auth(self) -> None:
        data, headers = _build_token_request(_config(auth_style="header"), "rt-123")
        expected = base64.b64encode(b"the-client-id:the-client-secret").decode()
        assert headers == {"Authorization": f"Basic {expected}"}

    def test_header_mode_omits_credentials_from_the_body(self) -> None:
        """RFC 6749 §2.3.1: a server MUST NOT accept more than one auth method.

        Sending the credentials in both places is not a harmless belt-and-braces
        — some providers reject the request outright.
        """
        data, _ = _build_token_request(_config(auth_style="header"), "rt-123")
        assert "client_id" not in data
        assert "client_secret" not in data
        assert data == {"grant_type": "refresh_token", "refresh_token": "rt-123"}

    def test_extra_params_cannot_overwrite_the_authorization_header(self) -> None:
        """extra_token_params is a BODY escape hatch and must stay one."""
        data, headers = _build_token_request(
            _config(
                auth_style="header",
                extra_token_params={"Authorization": "Basic attacker"},
            ),
            "rt-123",
        )
        expected = base64.b64encode(b"the-client-id:the-client-secret").decode()
        assert headers["Authorization"] == f"Basic {expected}"
        assert data["Authorization"] == "Basic attacker", (
            "the extra param lands in the body, where it is inert"
        )

    async def test_header_auth_reaches_the_wire_end_to_end(self) -> None:
        """The hook proven through the real refresh path, not just in isolation."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"access_token": "new-at", "expires_in": 3600})

        enc = FernetEncryptor(TEST_KEY)
        repo = InMemoryOAuthCredentialRepository()
        row = _row(enc, expires_at=_now() - timedelta(minutes=1))
        await repo.insert(row)
        svc = _service(
            repo, _FakeAudit(), handler=handler,
            providers={PROVIDER: _config(auth_style="header")},
        )

        await svc.ensure_fresh(org_id=ORG, provider=PROVIDER)

        expected = base64.b64encode(b"the-client-id:the-client-secret").decode()
        assert seen[0].headers["authorization"] == f"Basic {expected}"
        body = seen[0].content.decode()
        assert "client_secret" not in body, "secret must not also ride in the body"

    async def test_body_auth_reaches_the_wire_unchanged(self) -> None:
        """The regression counterpart of the test above, through the same path."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"access_token": "new-at", "expires_in": 3600})

        enc = FernetEncryptor(TEST_KEY)
        repo = InMemoryOAuthCredentialRepository()
        await repo.insert(_row(enc, expires_at=_now() - timedelta(minutes=1)))
        svc = _service(
            repo, _FakeAudit(), handler=handler, providers={PROVIDER: _config()},
        )

        await svc.ensure_fresh(org_id=ORG, provider=PROVIDER)

        assert "authorization" not in seen[0].headers
        assert "client_secret=the-client-secret" in seen[0].content.decode()


# ---------------------------------------------------------------------------
# 3. NEW: nullable expiry
# ---------------------------------------------------------------------------

class TestNullableExpiry:
    def test_null_expiry_is_valid(self) -> None:
        enc = FernetEncryptor(TEST_KEY)
        row = _row(enc, expires_at=None)
        assert evaluate_grant(row, now=_now()) is GrantStatus.VALID

    def test_null_expiry_is_still_valid_far_in_the_future(self) -> None:
        """'Does not expire' must not decay into 'expired' with the passage of
        time — there is no clock comparison to make for such a grant."""
        enc = FernetEncryptor(TEST_KEY)
        row = _row(enc, expires_at=None)
        distant = _now() + timedelta(days=3650)
        assert evaluate_grant(row, now=distant) is GrantStatus.VALID

    def test_null_expiry_without_a_refresh_token_is_still_valid(self) -> None:
        """The no-refresh-token branch is an EXPIRY-driven death. A grant that
        never expires never reaches it, so absence of a refresh token is not a
        defect for this provider shape — Notion issues one anyway, but the
        evaluator must not depend on that."""
        enc = FernetEncryptor(TEST_KEY)
        row = _row(enc, expires_at=None, refresh=None)
        assert evaluate_grant(row, now=_now()) is GrantStatus.VALID

    @pytest.mark.parametrize("state", ["revoked", "expired"])
    def test_terminal_state_still_beats_a_null_expiry(self, state: str) -> None:
        """THE load-bearing ordering assertion.

        For a non-expiring grant, `connection_state` is the ONLY thing that can
        make it dead. If the NULL branch were ever hoisted above the terminal
        check, a revoked Notion grant would be treated as usable forever.
        """
        enc = FernetEncryptor(TEST_KEY)
        row = _row(enc, expires_at=None, state=state)
        assert evaluate_grant(row, now=_now()) is GrantStatus.DEAD

    def test_expiring_grants_are_completely_unaffected(self) -> None:
        """Regression: the three pre-existing outcomes still classify identically."""
        enc = FernetEncryptor(TEST_KEY)
        assert evaluate_grant(
            _row(enc, expires_at=_now() + timedelta(hours=1)), now=_now()
        ) is GrantStatus.VALID
        assert evaluate_grant(
            _row(enc, expires_at=_now() + DEFAULT_EXPIRY_SKEW - timedelta(minutes=1)),
            now=_now(),
        ) is GrantStatus.NEEDS_REFRESH
        assert evaluate_grant(
            _row(enc, expires_at=_now() - timedelta(minutes=1), refresh=None),
            now=_now(),
        ) is GrantStatus.DEAD

    async def test_non_expiring_grant_never_hits_the_token_endpoint(self) -> None:
        """The behavioural consequence, and the reason §3 below has to exist."""
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, json={"access_token": "x", "expires_in": 3600})

        enc = FernetEncryptor(TEST_KEY)
        repo = InMemoryOAuthCredentialRepository()
        await repo.insert(_row(enc, expires_at=None))
        svc = _service(
            repo, _FakeAudit(), handler=handler, providers={PROVIDER: _config()}
        )

        await svc.ensure_fresh(org_id=ORG, provider=PROVIDER)
        assert calls == [], "a non-expiring grant must never trigger a refresh"

    async def test_a_parser_returning_none_persists_a_null_expiry(self) -> None:
        """A provider whose refresh response carries no expiry stays non-expiring
        after a refresh, rather than acquiring an invented timestamp."""

        def no_expiry_parser(payload: dict) -> TokenResponse:
            return TokenResponse(
                access_token=payload["access_token"],
                expires_in_seconds=None,
                refresh_token=payload.get("refresh_token"),
            )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"access_token": "new-at"})

        enc = FernetEncryptor(TEST_KEY)
        repo = InMemoryOAuthCredentialRepository()
        # Stored WITH an expiry so a refresh is actually triggered, then the
        # parser reports "no expiry" and the stored value must become NULL.
        await repo.insert(_row(enc, expires_at=_now() - timedelta(minutes=1)))
        svc = _service(
            repo, _FakeAudit(), handler=handler,
            providers={PROVIDER: _config(parse_token_response=no_expiry_parser)},
        )

        refreshed = await svc.ensure_fresh(org_id=ORG, provider=PROVIDER)
        assert refreshed.expires_at is None
        assert evaluate_grant(refreshed, now=_now()) is GrantStatus.VALID


# ---------------------------------------------------------------------------
# 4. NEW: live-API revocation — the gap nullable expiry opens
# ---------------------------------------------------------------------------

class TestLiveApiRevocation:
    """A non-expiring grant never refreshes, so the refresh-failure revocation
    path is unreachable for it. `mark_revoked_by_provider` is the only way its
    death can ever be recorded."""

    async def test_marks_a_valid_grant_revoked(self) -> None:
        enc = FernetEncryptor(TEST_KEY)
        repo = InMemoryOAuthCredentialRepository()
        await repo.insert(_row(enc, expires_at=None))
        audit = _FakeAudit()
        svc = _service(repo, audit)

        marked = await svc.mark_revoked_by_provider(
            org_id=ORG, provider=PROVIDER, reason="provider returned 401 unauthorized"
        )

        assert marked is True
        stored = await repo.get(ORG, PROVIDER, "")
        assert stored is not None
        assert stored.connection_state == "revoked"
        assert "401" in (stored.state_reason or "")

    async def test_the_next_gated_call_then_denies(self) -> None:
        """The end-to-end point of the method: state written here must make the
        NEXT `ensure_fresh` refuse, which is what the ToolProxy gate consumes."""
        from skylize.app.credentials.oauth import GrantRevoked

        enc = FernetEncryptor(TEST_KEY)
        repo = InMemoryOAuthCredentialRepository()
        await repo.insert(_row(enc, expires_at=None))
        svc = _service(repo, _FakeAudit())

        # Before: a non-expiring grant sails through.
        await svc.ensure_fresh(org_id=ORG, provider=PROVIDER)

        await svc.mark_revoked_by_provider(
            org_id=ORG, provider=PROVIDER, reason="revoked upstream"
        )

        with pytest.raises(GrantRevoked):
            await svc.ensure_fresh(org_id=ORG, provider=PROVIDER)

    async def test_is_idempotent(self) -> None:
        """A connector retrying a dead call must not spam state transitions."""
        enc = FernetEncryptor(TEST_KEY)
        repo = InMemoryOAuthCredentialRepository()
        await repo.insert(_row(enc, expires_at=None))
        audit = _FakeAudit()
        svc = _service(repo, audit)

        assert await svc.mark_revoked_by_provider(
            org_id=ORG, provider=PROVIDER, reason="first"
        ) is True
        assert await svc.mark_revoked_by_provider(
            org_id=ORG, provider=PROVIDER, reason="second"
        ) is False

        stored = await repo.get(ORG, PROVIDER, "")
        assert stored is not None
        assert stored.state_reason == "first", "the first reason must not be clobbered"

    async def test_unknown_grant_returns_false_rather_than_raising(self) -> None:
        """Runs inside a connector's error handling; it must not add a failure."""
        svc = _service(InMemoryOAuthCredentialRepository(), _FakeAudit())
        assert await svc.mark_revoked_by_provider(
            org_id=ORG, provider=PROVIDER, reason="whatever"
        ) is False

    async def test_it_audits_the_transition(self) -> None:
        enc = FernetEncryptor(TEST_KEY)
        repo = InMemoryOAuthCredentialRepository()
        await repo.insert(_row(enc, expires_at=None))
        audit = _FakeAudit()
        svc = _service(repo, audit)

        await svc.mark_revoked_by_provider(
            org_id=ORG, provider=PROVIDER, reason="401 from provider"
        )

        assert any(
            r.get("action_type") == "oauth.connection_state_changed"
            for r in audit.records
        ), "a destructive state change must leave an audit row"
