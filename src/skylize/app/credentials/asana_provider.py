"""Asana's `OAuthProviderConfig` — the entire Asana-specific OAuth surface.

This module is the whole of what Asana contributes to the provider-agnostic OAuth
infrastructure (`app/credentials/oauth.py`, f6360b4): an endpoint, the platform
client credentials, the scopes, and ONE overridden hook. There is no Asana-specific
refresh logic, storage, encryption, concurrency control, or revocation *handling*
here — all of that is the shared infrastructure's, and duplicating any of it
connector-side is what integration_inputs.md 2.6 forbids, exactly as 2.5 forbade it
for Drive.

TOKEN EXCHANGE NEEDS NO ADAPTATION AT ALL (integration_inputs.md 2.6, verified
2026-09-02 against `https://developers.asana.com/docs/oauth`). Asana is RFC 6749
§5.1-conformant on the wire: form-encoded `client_id`/`client_secret` in the POST
body — which is precisely what `_post_refresh` builds (`oauth.py:423-429`) — and a
token response carrying `access_token`, `expires_in: 3600`, `token_type: "bearer"`,
and `refresh_token`, which is precisely what `_default_parse_token_response` reads
(`oauth.py:104-127`). `parse_token_response` is therefore left at its default.

SCOPES (Q2.6a, `[UNVERIFIED MAPPING]` — read the docstring on `ASANA_SCOPES`).

WHY `is_revocation_error` IS OVERRIDDEN, AND WHY THAT IS *NOT* BECAUSE THE DEFAULT
IS BROKEN. The readiness audit predicted the default would never fire for Asana,
reasoning from Asana's REST error envelope `{"errors":[{"message": ...}]}`. Live
verification corrected that premise, and the correction is worth stating precisely
because a future author will otherwise re-derive the wrong conclusion:

  * `is_revocation_error` is called from exactly one place — `_classify_failure`
    (`oauth.py:469`), reached only from `_post_refresh` (`oauth.py:445`). It sees
    ONLY token-endpoint responses (`app.asana.com/-/oauth_token`), never REST API
    responses (`app.asana.com/api/1.0/*`).
  * Those two surfaces use DIFFERENT error formats. The REST API uses the `errors`
    array. The token endpoint returns RFC 6749:
    `400 {"error": "invalid_grant", "error_description": "The \\`refresh_token\\`
    provided was invalid."}` — evidenced verbatim in two Asana developer-forum
    threads (615160, 738321, both read 2026-09-02).

So the default WOULD have worked. This override ships as DEFENCE IN DEPTH, because
Asana does not *document* its token-endpoint error contract and a forum post is not
a specification. It is a strict superset of `_default_is_revocation`: identical on
the RFC 6749 shape, plus able to read the `errors` envelope should the token
endpoint ever return one.

THE LINE IT WILL NOT CROSS. A bare "Not Authorized" is NOT a revocation signal here.
At the token endpoint that response is equally consistent with a wrong platform
client secret — a Skylize misconfiguration — and reporting that to a customer as
"you revoked us" would demand a pointless reconnect and hide a platform outage
behind a per-tenant symptom. `oauth.py:23-26` states the rule this module obeys:
only an unambiguous provider signal may mark a grant dead.

Being provider-scoped, this override cannot affect Drive's, Slack's, or Stripe's
revocation detection: `_classify_failure` calls `config.is_revocation_error`, and
each provider carries its own config.
"""

from __future__ import annotations

from typing import Any

from .oauth import OAuthProviderConfig

#: Asana's OAuth 2.0 token endpoint (authorization-code exchange and refresh).
ASANA_TOKEN_URL = "https://app.asana.com/-/oauth_token"

#: The provider registry key. Matches the `provider` column in `oauth_credentials`
#: and the `ToolOAuthProfile.provider` the Asana tools declare.
ASANA_PROVIDER = "asana"

#: The narrowest granular scope set covering the four verbs in 2.6 Q2.6b.
#:
#: `[LIVE-VERIFIED]` 2026-09-02 against
#: `https://developers.asana.com/docs/oauth-scopes`: Asana publishes granular
#: `<resource>:<action>` scopes, space-delimited, and an app registered with "Full
#: permissions" instead uses `default` and CANNOT request specific scopes. Granular
#: is chosen so the attenuation-only principle stays enforceable and
#: `oauth_credentials.scopes` stays meaningful.
#:
#: TWO CAVEATS A FUTURE AUTHOR MUST NOT SILENTLY RESOLVE (2.6 Q2.6a):
#:   1. Asana does not document which scope covers
#:      `POST /projects/{gid}/addMembers`. `projects:write` is the plausible
#:      mapping — it mutates a project and returns a `ProjectResponse` — but the
#:      mapping is UNVERIFIED.
#:   2. There is NO `workspaces:write` scope, and no granular scope maps to
#:      `POST /workspaces/{gid}/addUser` at all. That verb appears to require Full
#:      permissions, which would grant every endpoint for every connected customer.
#:      Widening this tuple to `default` is a compliance and blast-radius decision,
#:      NOT a code change — see 2.6 Q2.6a before touching it.
ASANA_SCOPES: tuple[str, ...] = ("tasks:write", "projects:write")

#: Token-endpoint messages that unambiguously name the GRANT as the dead thing.
#: Matched case-insensitively as substrings of an Asana `errors[].message`, and only
#: at 400/401. Deliberately tiny and deliberately specific: every phrase here names
#: a refresh token or an authorization grant, never merely "unauthorized". Adding a
#: broader phrase would let a platform misconfiguration masquerade as a customer
#: revocation, which is the one failure `oauth.py`'s asymmetry exists to prevent.
_REVOCATION_PHRASES: tuple[str, ...] = (
    "refresh_token provided was invalid",
    "refresh token provided was invalid",
    "refresh_token is invalid",
    "refresh token is invalid",
    "refresh_token has been revoked",
    "refresh token has been revoked",
    "authorization has been revoked",
    "grant has been revoked",
)


def _normalize_message(message: str) -> str:
    """Lower-case, strip Asana's backtick quoting, and collapse whitespace.

    Asana quotes parameter names in its human-readable messages — the observed
    text is ``The `refresh_token` provided was invalid.`` — so a naive substring
    match against "refresh_token provided was invalid" silently fails. Caught by
    `test_asana_envelope_naming_the_grant`, which is why the phrase list below is
    written in plain words rather than trying to anticipate every quoting style.
    """
    return " ".join(message.replace("`", "").replace("'", "").lower().split())


def _asana_error_messages(payload: dict[str, Any]) -> list[str]:
    """Every `message` string in Asana's `{"errors":[{"message": ...}]}` envelope.

    Tolerant by design: a malformed or unexpected payload yields an empty list,
    which means "no revocation signal found" — the safe direction. This function
    must never raise, because it runs inside failure handling.
    """
    errors = payload.get("errors")
    if not isinstance(errors, list):
        return []
    messages: list[str] = []
    for item in errors:
        if isinstance(item, dict):
            message = item.get("message")
            if isinstance(message, str) and message:
                messages.append(message)
    return messages


def is_asana_revocation(status_code: int, payload: dict[str, Any]) -> bool:
    """Asana's revocation predicate. A strict superset of the RFC 6749 default.

    Returns True ONLY for an unambiguous dead-grant signal at 400/401:

      1. RFC 6749 `error == "invalid_grant"` — the shape Asana's token endpoint is
         observed to use, and the same condition `_default_is_revocation` applies.
      2. Failing that, an `errors[].message` naming a refresh token or grant as
         invalid/revoked — the defence-in-depth path, in case the token endpoint
         ever answers in the REST envelope.

    Everything else returns False and is therefore denied as `RefreshUnavailable`
    with the stored grant left untouched. In particular `invalid_client` returns
    False here and is handled explicitly downstream (`oauth.py:478-482`) as the
    Skylize misconfiguration it is, and a bare "Not Authorized" returns False
    because it cannot be told apart from that misconfiguration.
    """
    if status_code not in (400, 401):
        return False

    # 1. The RFC 6749 path — identical semantics to the shared default. Note the
    #    equality check: `invalid_request`, `invalid_scope`, and `invalid_client`
    #    are all also 400s and are OUR bugs, never the customer's revocation.
    if payload.get("error") == "invalid_grant":
        return True

    # 2. The Asana-envelope path. Only reached when no RFC 6749 `error` key said
    #    `invalid_grant`, so it can never override branch 1's judgement.
    for message in _asana_error_messages(payload):
        normalized = _normalize_message(message)
        if any(phrase in normalized for phrase in _REVOCATION_PHRASES):
            return True
    return False


def build_asana_provider_config(
    *, client_id: str, client_secret: str
) -> OAuthProviderConfig:
    """Build Asana's provider config from platform-level client credentials.

    Credentials are passed in by the composition root from `Settings`; this module
    never reads an environment variable itself, matching `google_provider.py` and
    every other integration in the tree.
    """
    return OAuthProviderConfig(
        provider=ASANA_PROVIDER,
        token_url=ASANA_TOKEN_URL,
        client_id=client_id,
        client_secret=client_secret,
        scopes=ASANA_SCOPES,
        # parse_token_response deliberately left at its default: Asana's token
        # response is RFC 6749 §5.1-conformant (see the module docstring).
        is_revocation_error=is_asana_revocation,
    )
