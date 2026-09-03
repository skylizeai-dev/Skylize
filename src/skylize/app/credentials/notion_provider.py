"""Notion's `OAuthProviderConfig` — the entire Notion-specific OAuth surface.

This module is the whole of what Notion contributes to the provider-agnostic
OAuth infrastructure (`app/credentials/oauth.py`): an endpoint, the platform
client credentials, and THREE overridden hooks. There is no Notion-specific
refresh logic, storage, encryption, concurrency control, or revocation
*handling* here — all of that is the shared infrastructure's.

Notion is the provider that motivated `8f147d4`'s two extensions, and it uses
both. All three overrides below are REQUIRED, not defence in depth — unlike
Asana, whose token endpoint turned out to be RFC 6749-conformant after all.

1. `auth_style="header"` — Notion authenticates the client with HTTP Basic.
   `[LIVE-VERIFIED]` 2026-09-03, https://developers.notion.com/reference/create-a-token:
   the endpoint declares `"security": [{"basicAuth": []}]` with
   `"basicAuth": {"type": "http", "scheme": "basic"}`, and the refresh request
   body carries ONLY `grant_type` and `refresh_token` — the credentials belong
   in the header, which is exactly what `auth_style="header"` produces.

2. `parse_token_response` — Notion's 200 response has NO `expires_in`.
   `[LIVE-VERIFIED]` same source; the full response is `access_token`,
   `token_type`, `refresh_token`, `bot_id`, `workspace_icon`, `workspace_name`,
   `workspace_id`, `owner`, `duplicated_template_id`, `request_id`. The shared
   `_default_parse_token_response` hard-requires a numeric `expires_in` and
   would raise `RefreshUnavailable` on every Notion response, so Notion supplies
   its own parser returning `expires_in_seconds=None` — "does not expire by
   time" (migration 0023), never "unknown".

3. `is_revocation_error` — Notion's token endpoint does NOT use RFC 6749's error
   shape. `[LIVE-VERIFIED]` it returns its own envelope,
   `{"object": "error", "code": "invalid_grant", "message": ..., "status": 400}`,
   so the shared default (which reads a top-level `"error"` key) would find
   `None` and NEVER detect a dead grant. This override reads `code` instead.

   THIS IS A DIFFERENT FINDING FROM ASANA'S, and the difference matters. Asana's
   REST API uses a custom envelope but its TOKEN endpoint is RFC 6749, so its
   override ships as defence in depth. Notion's token endpoint is custom, so its
   override is load-bearing: without it, a revoked Notion grant is undetectable
   on the refresh path.

THE COMPOUNDING RISK, stated plainly. A Notion grant never expires (2), so
`evaluate_grant` never asks for a refresh, so the refresh path — and hook (3)
with it — is unreachable in normal operation. Hook (3) only fires if a refresh
is somehow attempted at all. The revocation signal Notion actually gives us in
practice is a 401 on a live REST call, which is why `tools/builtin/notion_tools.py`
calls `OAuthCredentialService.mark_revoked_by_provider`. Hook (3) exists so the
refresh path is correct IF reached; the connector's 401 handling is what makes
revocation detectable at all.
"""

from __future__ import annotations

from typing import Any

from .oauth import OAuthProviderConfig, RefreshUnavailable, TokenResponse

#: Notion's OAuth 2.0 token endpoint (authorization-code exchange and refresh).
NOTION_TOKEN_URL = "https://api.notion.com/v1/oauth/token"

#: The provider registry key. Matches the `provider` column in
#: `oauth_credentials` and the `ToolOAuthProfile.provider` the Notion tools
#: declare. Already named as the example second provider in
#: `tools/base.py`'s `ToolOAuthProfile` docstring, from long before this landed.
NOTION_PROVIDER = "notion"

#: Notion has NO OAuth `scope` parameter. `[LIVE-VERIFIED]` 2026-09-03,
#: https://developers.notion.com/reference/capabilities: an integration's
#: capabilities (Read content / Update content / Insert content / Read comments /
#: Insert comments, plus a three-way user-information setting) are fixed when the
#: integration is REGISTERED in Notion's developer portal, not requested
#: per-authorization.
#:
#: Two consequences a future author must not try to "fix" in code:
#:   * `oauth_credentials.scopes` stays empty for Notion. The token response
#:     carries no `scope` field, so there is nothing to record. This means the
#:     attenuation invariant that column exists to make checkable is NOT
#:     checkable for Notion — a real, if narrow, governance regression relative
#:     to Drive's `drive.file` and Asana's `projects:write`, and one no code
#:     change here can recover.
#:   * The least-privilege decision for Notion is WHICH CAPABILITIES to register
#:     the Skylize integration with, made once in a portal. See 2.7 Q2.7a.
NOTION_SCOPES: tuple[str, ...] = ()

#: Sent on every Notion API request; the API refuses requests without it.
#: `[LIVE-VERIFIED]` 2026-09-02 against
#: https://developers.notion.com/reference/post-page, which names this as "The
#: latest version". Pinned deliberately rather than tracking "latest": a version
#: bump can change response shapes, so moving it is a reviewed change, not a
#: silent one. See 2.7 Q2.7d.
NOTION_VERSION = "2026-03-11"


def parse_notion_token_response(payload: dict[str, Any]) -> TokenResponse:
    """Notion's token response — RFC 6749 §5.1 minus any expiry.

    Returns `expires_in_seconds=None`, which the infrastructure persists as
    `expires_at IS NULL` (migration 0023). That is a POSITIVE assertion that the
    grant does not expire by time, not a fallback for a value we failed to read.

    Deliberately still strict about `access_token`: a response without a usable
    one is an error, exactly as in the shared default. Only the expiry
    requirement is relaxed, and only because Notion documents its absence.
    """
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise RefreshUnavailable(
            "Notion token response carried no usable access_token"
        )
    new_refresh = payload.get("refresh_token")
    return TokenResponse(
        access_token=access_token,
        # Notion issues no expiry. See the module docstring.
        expires_in_seconds=None,
        refresh_token=(
            new_refresh if isinstance(new_refresh, str) and new_refresh else None
        ),
        # No `scope` field exists on a Notion token response.
        scopes=(),
    )


def is_notion_revocation(status_code: int, payload: dict[str, Any]) -> bool:
    """Notion's token-endpoint revocation predicate.

    Notion reports the error in `code`, not in RFC 6749's top-level `error`:
    `{"object": "error", "code": "invalid_grant", "message": ..., "status": 400}`.
    The shared default reads `error` and so would never fire here.

    Returns True ONLY for an unambiguous dead-grant signal at 400/401. As narrow
    as the shared default and for the same reason: marking a grant revoked is
    destructive to the customer, so `invalid_client` (a Skylize misconfiguration —
    our client id or secret is wrong, which Notion returns at 401) and
    `invalid_request` (our bug) must never be reported as the customer revoking
    us. Both return False and are denied downstream as `RefreshUnavailable`.

    The RFC 6749 `error` key is also honoured, purely so this predicate keeps
    working if Notion ever moves to the standard shape. It can only ever widen
    detection, never narrow it.
    """
    if status_code not in (400, 401):
        return False
    return (
        payload.get("code") == "invalid_grant"
        or payload.get("error") == "invalid_grant"
    )


def build_notion_provider_config(
    *, client_id: str, client_secret: str
) -> OAuthProviderConfig:
    """Build Notion's provider config from platform-level client credentials.

    Credentials are passed in by the composition root from `Settings`; this
    module never reads an environment variable itself, matching
    `google_provider.py` and `asana_provider.py`.
    """
    return OAuthProviderConfig(
        provider=NOTION_PROVIDER,
        token_url=NOTION_TOKEN_URL,
        client_id=client_id,
        client_secret=client_secret,
        scopes=NOTION_SCOPES,
        parse_token_response=parse_notion_token_response,
        is_revocation_error=is_notion_revocation,
        # HTTP Basic, credentials out of the body. The whole reason 8f147d4
        # added this field.
        auth_style="header",
    )
