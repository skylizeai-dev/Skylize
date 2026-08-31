"""Google's `OAuthProviderConfig` — the entire Google-specific OAuth surface.

This module is the whole of what Drive contributes to the provider-agnostic OAuth
infrastructure (`app/credentials/oauth.py`, f6360b4): an endpoint, the platform
client credentials, and the scope. There is no Google-specific refresh logic,
storage, encryption, concurrency control, or revocation handling here — all of
that is the shared infrastructure's, and duplicating any of it Drive-side is
exactly what integration_inputs.md 2.5 forbids.

Google's token endpoint returns a standard RFC 6749 response and signals a dead
grant with `invalid_grant`, so the infrastructure's DEFAULT parser and default
revocation predicate both apply unchanged — this config overrides neither.

SCOPE (Q2.5a, `[VERIFIED]` 2026-08-29 against
`https://developers.google.com/workspace/drive/api/guides/api-specific-auth`):
`drive.file` only. It is listed non-sensitive / recommended and needs basic app
verification; full `drive` and `drive.readonly` are Restricted and pull the app
into CASA with an annual reassessment. Widening this constant is a cost and
compliance decision, not a code change — see 2.5 before touching it.
"""

from __future__ import annotations

from .oauth import OAuthProviderConfig

#: Google's OAuth 2.0 token endpoint (authorization-code exchange and refresh).
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

#: The ONLY Drive scope this platform requests. See the module docstring.
DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"

DRIVE_PROVIDER = "google_drive"


def build_google_drive_provider_config(
    *, client_id: str, client_secret: str
) -> OAuthProviderConfig:
    """Build Drive's provider config from platform-level client credentials.

    Credentials are passed in by the composition root from `Settings`; this module
    never reads an environment variable itself, matching every other integration
    in the tree.
    """
    return OAuthProviderConfig(
        provider=DRIVE_PROVIDER,
        token_url=GOOGLE_TOKEN_URL,
        client_id=client_id,
        client_secret=client_secret,
        scopes=(DRIVE_FILE_SCOPE,),
        # parse_token_response / is_revocation_error deliberately left at their
        # defaults: Google is RFC-6749-conformant on both.
    )
