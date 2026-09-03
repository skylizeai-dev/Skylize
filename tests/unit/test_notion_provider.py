"""Notion's OAuthProviderConfig — the first consumer of both 8f147d4 extensions.

Notion is the provider those extensions were built for, and it is the only one
that needs all three hooks. Each test below pins one live-verified fact about
Notion's OAuth surface, so if Notion changes shape the failure names the reason
rather than surfacing as a mystery 401 in production.
"""

from __future__ import annotations

import base64

import pytest

from skylize.app.credentials.notion_provider import (
    NOTION_PROVIDER,
    NOTION_SCOPES,
    NOTION_TOKEN_URL,
    NOTION_VERSION,
    build_notion_provider_config,
    is_notion_revocation,
    parse_notion_token_response,
)
from skylize.app.credentials.oauth import (
    GrantStatus,
    RefreshUnavailable,
    _build_token_request,
    _default_is_revocation,
    _default_parse_token_response,
    evaluate_grant,
)

#: The 200 body Notion documents, verbatim in shape (live-verified 2026-09-03
#: against developers.notion.com/reference/create-a-token). Note: NO expires_in.
NOTION_TOKEN_200 = {
    "access_token": "ntn_abc123",
    "token_type": "bearer",
    "refresh_token": "ntn_refresh_xyz",
    "bot_id": "bot-uuid",
    "workspace_id": "ws-uuid",
    "workspace_name": "Acme",
    "workspace_icon": None,
    "owner": {"type": "user"},
    "duplicated_template_id": None,
    "request_id": "req-uuid",
}


class TestAuthStyle:
    def test_notion_uses_header_auth(self) -> None:
        cfg = build_notion_provider_config(client_id="cid", client_secret="sec")
        assert cfg.auth_style == "header"

    def test_credentials_go_in_a_basic_header_not_the_body(self) -> None:
        cfg = build_notion_provider_config(client_id="cid", client_secret="sec")
        data, headers = _build_token_request(cfg, "rt-1")

        expected = base64.b64encode(b"cid:sec").decode()
        assert headers["Authorization"] == f"Basic {expected}"
        assert "client_id" not in data and "client_secret" not in data
        # Notion's documented refresh body is exactly these two fields.
        assert data == {"grant_type": "refresh_token", "refresh_token": "rt-1"}

    def test_token_url(self) -> None:
        cfg = build_notion_provider_config(client_id="c", client_secret="s")
        assert cfg.token_url == NOTION_TOKEN_URL == "https://api.notion.com/v1/oauth/token"


class TestTokenParsing:
    def test_the_shared_default_would_reject_notions_response(self) -> None:
        """Why a custom parser is REQUIRED, not stylistic."""
        with pytest.raises(RefreshUnavailable, match="expires_in"):
            _default_parse_token_response(NOTION_TOKEN_200)

    def test_notion_parser_yields_no_expiry(self) -> None:
        token = parse_notion_token_response(NOTION_TOKEN_200)
        assert token.expires_in_seconds is None, (
            "None is a positive assertion that the grant does not expire"
        )
        assert token.access_token == "ntn_abc123"
        assert token.refresh_token == "ntn_refresh_xyz"
        assert token.scopes == (), "Notion has no OAuth scopes"

    def test_parser_is_still_strict_about_the_access_token(self) -> None:
        """Only the expiry requirement is relaxed."""
        with pytest.raises(RefreshUnavailable, match="access_token"):
            parse_notion_token_response({"token_type": "bearer"})

    def test_absent_refresh_token_is_none_not_a_crash(self) -> None:
        token = parse_notion_token_response({"access_token": "at"})
        assert token.refresh_token is None

    def test_a_parsed_notion_grant_evaluates_valid_forever(self) -> None:
        """End-to-end meaning of expires_in_seconds=None, through the real evaluator."""
        import uuid
        from datetime import datetime, timedelta, timezone

        from skylize.dal.oauth_credentials import OAuthCredentialRow

        now = datetime.now(timezone.utc)
        row = OAuthCredentialRow(
            cred_id=uuid.uuid4(), org_id="o", provider=NOTION_PROVIDER, label="",
            provider_account_id="ws", key_id="k",
            encrypted_access_token="ct", encrypted_refresh_token="ct",
            expires_at=None, scopes=(), connection_state="valid",
            state_reason=None, created_at=now, updated_at=now, refreshed_at=None,
        )
        assert evaluate_grant(row, now=now) is GrantStatus.VALID
        assert evaluate_grant(row, now=now + timedelta(days=3650)) is GrantStatus.VALID


class TestRevocationPredicate:
    def test_the_shared_default_would_never_fire_for_notion(self) -> None:
        """The finding that makes this override load-bearing rather than
        defence-in-depth: Notion's token endpoint reports the error in `code`,
        not in RFC 6749's top-level `error` key."""
        notion_400 = {
            "object": "error", "status": 400,
            "code": "invalid_grant", "message": "Invalid refresh token.",
        }
        assert _default_is_revocation(400, notion_400) is False
        assert is_notion_revocation(400, notion_400) is True

    @pytest.mark.parametrize("status", [400, 401])
    def test_invalid_grant_is_revocation(self, status: int) -> None:
        assert is_notion_revocation(status, {"code": "invalid_grant"}) is True

    def test_invalid_client_is_never_revocation(self) -> None:
        """Notion returns this at 401 when OUR client id/secret is wrong. Telling
        a customer they revoked us would hide a platform misconfiguration."""
        assert is_notion_revocation(401, {"code": "invalid_client"}) is False

    @pytest.mark.parametrize(
        "code", ["invalid_request", "validation_error", "unauthorized", "rate_limited"]
    )
    def test_other_codes_are_not_revocation(self, code: str) -> None:
        assert is_notion_revocation(400, {"code": code}) is False

    @pytest.mark.parametrize("status", [200, 403, 404, 429, 500, 529])
    def test_non_auth_statuses_are_never_revocation(self, status: int) -> None:
        assert is_notion_revocation(status, {"code": "invalid_grant"}) is False

    def test_rfc6749_shape_also_honoured(self) -> None:
        """Forward-compatible if Notion ever adopts the standard shape; can only
        widen detection, never narrow it."""
        assert is_notion_revocation(400, {"error": "invalid_grant"}) is True

    @pytest.mark.parametrize("payload", [{}, {"code": None}, {"object": "error"}])
    def test_malformed_payloads_never_revoke(self, payload) -> None:
        assert is_notion_revocation(400, payload) is False


class TestConfigWiring:
    def test_all_three_hooks_are_overridden(self) -> None:
        cfg = build_notion_provider_config(client_id="c", client_secret="s")
        assert cfg.parse_token_response is parse_notion_token_response
        assert cfg.is_revocation_error is is_notion_revocation
        assert cfg.auth_style == "header"

    def test_notion_requests_no_scopes(self) -> None:
        """Capabilities are fixed at integration registration, not per-grant."""
        assert NOTION_SCOPES == ()

    def test_api_version_is_pinned(self) -> None:
        """A version bump can change response shapes; moving it is a reviewed
        change (2.7 Q2.7d), not something that drifts."""
        assert NOTION_VERSION == "2026-03-11"

    def test_overrides_do_not_leak_into_other_providers(self) -> None:
        from skylize.app.credentials.asana_provider import build_asana_provider_config
        from skylize.app.credentials.google_provider import (
            build_google_drive_provider_config,
        )

        for build in (build_google_drive_provider_config, build_asana_provider_config):
            other = build(client_id="c", client_secret="s")
            assert other.auth_style == "body", f"{other.provider} lost body auth"
            assert other.parse_token_response is _default_parse_token_response
            assert other.is_revocation_error is not is_notion_revocation
