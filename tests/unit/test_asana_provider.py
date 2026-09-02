"""Asana's OAuthProviderConfig — revocation detection and config shape.

The centre of gravity here is `is_asana_revocation`. Marking a grant 'revoked' is
destructive to a customer (it demands a reconnect), so these tests assert BOTH
directions: it fires on an unambiguous dead-grant signal, and it refuses to fire on
anything that could equally be a Skylize misconfiguration or a transient fault.

The readiness audit predicted the shared default would never detect a dead Asana
grant, reasoning from Asana's REST envelope. `test_default_predicate_*` below records
what live verification actually found — the token endpoint is RFC 6749 — so the
corrected premise is pinned by a test rather than left in prose that can drift.
"""

from __future__ import annotations

import pytest

from skylize.app.credentials.asana_provider import (
    ASANA_PROVIDER,
    ASANA_SCOPES,
    ASANA_TOKEN_URL,
    build_asana_provider_config,
    is_asana_revocation,
)
from skylize.app.credentials.oauth import (
    _default_is_revocation,
    _default_parse_token_response,
)

# --- The RFC 6749 path: what Asana's token endpoint is observed to return -------
# Verbatim shape from Asana developer-forum threads 615160 and 738321 (read
# 2026-09-02), both showing HTTP 400 from app.asana.com/-/oauth_token.
ASANA_REVOKED_PAYLOAD = {
    "error": "invalid_grant",
    "error_uri": "https://asana.com/developers/documentation/getting-started/authentication",
    "error_description": "The `refresh_token` provided was invalid.",
}


class TestRevocationFires:
    """Signals that MUST mark the grant dead."""

    def test_rfc6749_invalid_grant_at_400(self) -> None:
        assert is_asana_revocation(400, ASANA_REVOKED_PAYLOAD) is True

    def test_rfc6749_invalid_grant_at_401(self) -> None:
        assert is_asana_revocation(401, {"error": "invalid_grant"}) is True

    @pytest.mark.parametrize(
        "message",
        [
            "The `refresh_token` provided was invalid.",
            "refresh token provided was invalid",
            "The refresh_token is invalid",
            "This authorization has been revoked by the user",
        ],
    )
    def test_asana_envelope_naming_the_grant(self, message: str) -> None:
        """Defence in depth: the `errors` envelope, should the token endpoint use it.

        This is the branch the shared default cannot reach, because the default
        only ever reads a top-level `error` key.
        """
        payload = {"errors": [{"message": message}]}
        assert is_asana_revocation(400, payload) is True
        assert _default_is_revocation(400, payload) is False, (
            "if the default already caught this, the override would be redundant"
        )

    def test_envelope_match_is_case_insensitive(self) -> None:
        payload = {"errors": [{"message": "THE REFRESH TOKEN IS INVALID"}]}
        assert is_asana_revocation(400, payload) is True

    def test_matches_when_one_of_several_errors_names_the_grant(self) -> None:
        payload = {
            "errors": [
                {"message": "Something unrelated"},
                {"message": "The refresh_token provided was invalid."},
            ]
        }
        assert is_asana_revocation(400, payload) is True


class TestRevocationRefusesToFire:
    """Signals that must NEVER mark a customer's grant dead.

    Each case here is one a careless widening of `_REVOCATION_PHRASES` would break,
    and each would cause a real customer to be told to reconnect a connection they
    never revoked.
    """

    def test_bare_not_authorized_is_not_revocation(self) -> None:
        """The load-bearing refusal.

        At the token endpoint a bare 401 is equally consistent with a wrong platform
        client secret. Treating it as revocation would hide a platform-wide outage
        behind a per-tenant "you disconnected us" message.
        """
        payload = {"errors": [{"message": "Not Authorized"}]}
        assert is_asana_revocation(401, payload) is False

    def test_invalid_client_is_not_revocation(self) -> None:
        """Our misconfiguration. Handled downstream as RefreshUnavailable."""
        assert is_asana_revocation(400, {"error": "invalid_client"}) is False

    @pytest.mark.parametrize("error", ["invalid_request", "invalid_scope", "unsupported_grant_type"])
    def test_other_rfc6749_errors_are_our_bugs(self, error: str) -> None:
        assert is_asana_revocation(400, {"error": error}) is False

    def test_missing_refresh_token_parameter_is_our_bug_not_revocation(self) -> None:
        """Observed verbatim in forum thread 615160 — an `invalid_request`, our bug.

        The message names `refresh_token`, so a naive substring rule like
        "refresh_token" alone would misclassify it. The phrase list is specific
        enough to say 'was invalid'/'is invalid', not merely to mention the token.
        """
        payload = {
            "error": "invalid_request",
            "error_description": "The `refresh_token` parameter was missing.",
        }
        assert is_asana_revocation(400, payload) is False

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_transient_statuses_are_never_revocation(self, status: int) -> None:
        assert is_asana_revocation(status, ASANA_REVOKED_PAYLOAD) is False

    def test_200_is_never_revocation(self) -> None:
        assert is_asana_revocation(200, ASANA_REVOKED_PAYLOAD) is False

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"errors": "not-a-list"},
            {"errors": []},
            {"errors": [None, 42, "string"]},
            {"errors": [{"no_message_key": "x"}]},
            {"errors": [{"message": None}]},
            {"errors": [{"message": ""}]},
        ],
    )
    def test_malformed_payloads_never_raise_and_never_revoke(self, payload) -> None:
        """This predicate runs inside failure handling; it must not add a failure."""
        assert is_asana_revocation(400, payload) is False


class TestOverrideIsAStrictSuperset:
    """The override may never be LESS willing to detect revocation than the default.

    If a future edit narrows it below the shared default, Asana would regress into
    exactly the silent degradation the audit warned about.
    """

    @pytest.mark.parametrize("status", [400, 401])
    @pytest.mark.parametrize(
        "payload",
        [
            {"error": "invalid_grant"},
            ASANA_REVOKED_PAYLOAD,
            {"error": "invalid_client"},
            {"error": "invalid_request"},
            {},
        ],
    )
    def test_superset_of_default(self, status: int, payload) -> None:
        if _default_is_revocation(status, payload):
            assert is_asana_revocation(status, payload), (
                "override missed a revocation the shared default would have caught"
            )


class TestProviderConfig:
    def test_config_shape(self) -> None:
        config = build_asana_provider_config(client_id="cid", client_secret="secret")
        assert config.provider == ASANA_PROVIDER == "asana"
        assert config.token_url == ASANA_TOKEN_URL
        assert config.client_id == "cid"
        assert config.client_secret == "secret"
        assert config.scopes == ASANA_SCOPES

    def test_parse_token_response_left_at_default(self) -> None:
        """Asana is RFC 6749 §5.1-conformant, so no parser override is warranted."""
        config = build_asana_provider_config(client_id="c", client_secret="s")
        assert config.parse_token_response is _default_parse_token_response

    def test_revocation_hook_is_the_asana_override(self) -> None:
        config = build_asana_provider_config(client_id="c", client_secret="s")
        assert config.is_revocation_error is is_asana_revocation
        assert config.is_revocation_error is not _default_is_revocation

    def test_live_token_response_parses_with_the_shared_default(self) -> None:
        """The exact 200 body Asana's OAuth docs publish, through the shared parser.

        This is the test that justifies "zero changes to the OAuth primitive": if it
        ever fails, Asana's wire format moved and the claim needs re-verifying.
        """
        payload = {
            "access_token": "f6ds7fdsa69ags7ag9sd5a",
            "expires_in": 3600,
            "token_type": "bearer",
            "data": {"id": 4673218951, "gid": "4673218951", "name": "Greg Sanchez"},
            "refresh_token": "hjkl325hjkl4325hj4kl32fjds",
        }
        token = _default_parse_token_response(payload)
        assert token.access_token == "f6ds7fdsa69ags7ag9sd5a"
        assert token.expires_in_seconds == 3600
        assert token.refresh_token == "hjkl325hjkl4325hj4kl32fjds"

    def test_scopes_do_not_silently_include_full_permissions(self) -> None:
        """2.6 Q2.6a: widening to `default` is an owner decision, not a code change."""
        assert "default" not in ASANA_SCOPES
        assert ASANA_SCOPES == ("tasks:write", "projects:write")

    def test_override_does_not_leak_into_other_providers(self) -> None:
        """Provider-scoped by construction — Drive must keep the shared default."""
        from skylize.app.credentials.google_provider import (
            build_google_drive_provider_config,
        )

        drive = build_google_drive_provider_config(client_id="c", client_secret="s")
        assert drive.is_revocation_error is _default_is_revocation
        assert drive.is_revocation_error is not is_asana_revocation
