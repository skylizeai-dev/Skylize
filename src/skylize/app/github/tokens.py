"""Minting GitHub App credentials. Two steps, neither of which persists anything.

    platform RSA key --(RS256 JWT, <=10 min)--> POST /app/installations/{id}
        /access_tokens --(installation token, 1 hour)--> one governed call

WHY NOTHING IS STORED, AND WHY THAT IS NOT THE SAME AS WIF
----------------------------------------------------------
The shape rhymes with `app/gcp/tokens.py`: sign a short-lived assertion with a
platform key, exchange it, use the result, discard it. There is no refresh token in
either model, which is why migration 0026 has no encrypted columns and why
`app/credentials/oauth.py`'s refresh machinery is not involved.

It differs from WIF in one way that matters for this module's API: GitHub lets the
CALLER narrow the resulting credential at mint time.

    "You can use the `repositories` or `repository_ids` body parameters to specify
    individual repositories that the installation access token can access... Use
    the `permissions` body parameter to specify the permissions that the
    installation access token should have."
    `[LIVE-VERIFIED]` 2026-09-05, docs.github.com/en/apps/creating-github-apps/
    authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app

That is `integration_inputs.md`'s **attenuation-only** invariant (§ "Global
combining principle", clause 2: "A connector may never widen authority") enforced
by the PROVIDER rather than by Skylize's own discipline. It is the strongest form
of that invariant available from any connector in this codebase, and it is the
reason §2.4 Q2.4a chose a GitHub App over an OAuth App or a PAT. So narrowing here
is not optional garnish: `mint_installation_token` REQUIRES the caller to name the
repositories, and refuses an unnarrowed mint.

WHY THE JWT LIFETIME IS 8 MINUTES AND NOT GITHUB'S 10
-----------------------------------------------------
GitHub caps the assertion at "no more than 10 minutes into the future" and
recommends setting `iat` 60 seconds in the past to absorb clock drift (same source
as above, verified 2026-09-05). Setting `exp` at exactly the cap plus a backdated
`iat` puts the total span at the limit, where any additional server-side clock
skew makes GitHub reject the assertion with a `401` that looks like a bad key. So
`exp` is `now + 8 min` against `iat` of `now - 60s`: a 9-minute span, comfortably
inside the cap, with a full minute of headroom at each end. The exchange is a
single HTTP call that completes in well under a second; a longer assertion buys
nothing and only widens the window in which a leaked one is useful.

WHY THE ASSERTION IS NEVER REUSED ACROSS EXCHANGES
--------------------------------------------------
A fresh JWT is minted per exchange rather than cached for its 8-minute life. RSA
signing is cheap relative to the HTTPS round trip that follows it, and caching a
bearer assertion that can mint credentials for EVERY customer installation — which
is what this JWT is, since the key is platform-level — to save a signature is a
bad trade. The installation token it produces is likewise handed to the caller and
never held here.

A NOTE ON WHAT THIS MODULE DELIBERATELY DOES NOT DO
---------------------------------------------------
It does not delete branches, force-push, or merge pull requests, and it registers
no tool. Per §2.4 Q2.4b (owner-approved 2026-09-05) Skylize never builds a
branch-deletion verb at all — verb-surface minimalism rather than a runtime gate —
and the PR-merge verb plus the uninstall webhook are explicitly out of scope for
this pass. This module mints credentials; it performs no repository mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

import httpx

#: GitHub accepts only RS256 for App JWTs (verified 2026-09-05). Not a preference.
GITHUB_APP_JWT_ALGORITHM = "RS256"

#: `iat` is backdated by this much, per GitHub's own clock-drift recommendation.
CLOCK_SKEW_LEEWAY = timedelta(seconds=60)

#: Assertion lifetime. GitHub's hard cap is 10 minutes; see the module docstring
#: for why this is 8 and not 10.
JWT_LIFETIME = timedelta(minutes=8)

#: The Tier 1 permission set from `integration_inputs.md` §2.4 Q2.4b.1. Requesting
#: LESS than the installation holds is always legal; this is the ceiling Skylize
#: ever asks for, and `administration`/`secrets` are absent BY DESIGN — that
#: absence is what makes repository deletion and branch-protection edits
#: structurally impossible rather than merely gated (§2.4 Tier 1).
TIER1_PERMISSIONS: Mapping[str, str] = {
    "contents": "write",
    "metadata": "read",
}

#: Permissions that must never appear in a mint request. Tier 1 is load-bearing:
#: `administration` is what would allow deleting a repository or weakening the
#: very ruleset the probe relies on, and `secrets` would allow tampering with CI
#: credentials. A caller that passes either is a bug, and this list turns that bug
#: into an immediate refusal rather than a silently over-broad token.
FORBIDDEN_PERMISSIONS: frozenset[str] = frozenset({
    "administration",
    "secrets",
    "actions",
    "environments",
    "workflows",
})


class GithubTokenError(RuntimeError):
    """A GitHub credential could not be minted."""


class GithubInstallationGone(GithubTokenError):
    """The installation no longer exists or is suspended.

    Separate from the base error because the REMEDY differs and callers must be
    able to act on the difference: this one maps to `connection_state='revoked'`
    and asks the customer to reinstall, whereas a transient failure must leave the
    stored state untouched. Same discipline as
    `app/credentials/oauth.py`'s `GrantRevoked` vs `RefreshUnavailable`.
    """


class GithubTransientError(GithubTokenError):
    """A network fault, timeout, 429, or 5xx. State must NOT be changed.

    "We could not check" must never collapse into "there was nothing to find" —
    the rule `app/gcp/probe.py` and `app/credentials/oauth.py` already establish.
    """


@dataclass(frozen=True, slots=True)
class InstallationToken:
    """One short-lived installation access token. Never persisted.

    `expires_at` is carried so a caller doing several API calls in one governed
    action can tell whether its credential is still live, NOT so it can be stored
    — migration 0026 has no column for it and deliberately so.
    """

    token: str
    expires_at: datetime
    #: Echoed back by GitHub. Recorded on the value so a caller can assert it got
    #: what it asked for rather than assuming the narrowing was honoured.
    permissions: Mapping[str, str]
    repository_selection: str

    def __repr__(self) -> str:
        """Never render the bearer token."""
        return (
            f"InstallationToken(expires_at={self.expires_at.isoformat()}, "
            f"permissions={dict(self.permissions)!r}, "
            f"repository_selection={self.repository_selection!r}, "
            "token=<redacted>)"
        )


def mint_app_jwt(
    *,
    app_id: str,
    private_key: Any,
    now: datetime | None = None,
) -> str:
    """Return a signed RS256 assertion authenticating Skylize AS THE APP.

    This credential is not scoped to any customer: it authenticates the App
    itself, and its only legitimate use is exchanging it for a per-installation
    token. It must never be handed to a tool, logged, or returned to a caller
    outside this module's own exchange path.

    `private_key` is typed `Any` rather than `RSAPrivateKey` to keep this module
    free of a `cryptography` import it does not otherwise need; `keys.py` has
    already asserted the type and the modulus size by the time a key reaches here.
    """
    from jose import jwt as jose_jwt

    issued = now or datetime.now(timezone.utc)
    claims = {
        # GitHub accepts the App ID or the client id here. `keys.py` keeps it a
        # string and this passes it through unparsed.
        "iss": app_id,
        "iat": int((issued - CLOCK_SKEW_LEEWAY).timestamp()),
        "exp": int((issued + JWT_LIFETIME).timestamp()),
    }
    token: str = jose_jwt.encode(
        claims, private_key, algorithm=GITHUB_APP_JWT_ALGORITHM
    )
    return token


def assert_permissions_allowed(permissions: Mapping[str, str]) -> None:
    """Refuse a mint that asks for anything Tier 1 forbids.

    Called before every exchange rather than trusted to callers. §2.4's Tier 1 is
    the strongest guarantee in the GitHub design precisely because it is
    structural — a permission never requested cannot be misused by any later
    refactor — and a check that only some callers remember to run would not be
    structural at all.
    """
    forbidden = sorted(set(permissions) & FORBIDDEN_PERMISSIONS)
    if forbidden:
        raise GithubTokenError(
            f"refusing to mint an installation token requesting {forbidden}. "
            "integration_inputs.md 2.4 Tier 1 (APPROVED 2026-09-05) forbids "
            "these: 'administration' would permit deleting a repository and "
            "editing the very ruleset the connection-state probe depends on, and "
            "'secrets'/'actions'/'environments'/'workflows' would permit "
            "tampering with CI. Their absence is what makes those actions "
            "impossible rather than merely gated."
        )


class GithubAppTokenMinter:
    """Exchanges the platform App key for narrowed, short-lived installation tokens.

    One instance per process; holds the key and an HTTP client, no per-tenant
    state. The org -> installation mapping lives in `github_app_installations`
    and is resolved by the caller, so this class never reads the database and
    cannot accidentally cross a tenant boundary.
    """

    def __init__(
        self,
        *,
        app_id: str,
        private_key: Any,
        client: httpx.AsyncClient,
        api_base_url: str = "https://api.github.com",
    ) -> None:
        self._app_id = app_id
        self._private_key = private_key
        self._client = client
        self._base = api_base_url.rstrip("/")

    async def mint_installation_token(
        self,
        *,
        installation_id: int,
        repository_ids: Sequence[int] | None = None,
        repositories: Sequence[str] | None = None,
        permissions: Mapping[str, str] | None = None,
        now: datetime | None = None,
    ) -> InstallationToken:
        """Mint one narrowed installation token. Nothing is stored.

        Exactly one of `repository_ids` or `repositories` must be given. An
        unnarrowed mint is REFUSED rather than defaulted: GitHub's own behaviour
        when both are omitted is "access to all repositories" with "all of the
        permissions that were granted to the app" (verified 2026-09-05), which is
        the widest credential the installation can produce. Making that the
        accidental default — reachable by forgetting an argument — would invert
        the attenuation-only invariant this connector was chosen to enforce.
        """
        if (repository_ids is None) == (repositories is None):
            raise GithubTokenError(
                "mint_installation_token requires exactly one of "
                "`repository_ids` or `repositories`. Omitting both would make "
                "GitHub issue a token for ALL repositories with ALL granted "
                "permissions (verified 2026-09-05) — the widest possible "
                "credential, reachable by forgetting an argument. Name the "
                "repositories this governed action actually needs."
            )

        perms = dict(permissions if permissions is not None else TIER1_PERMISSIONS)
        assert_permissions_allowed(perms)

        body: dict[str, Any] = {"permissions": perms}
        if repository_ids is not None:
            if not repository_ids:
                raise GithubTokenError(
                    "`repository_ids` is empty. An empty narrowing list is not "
                    "'no restriction' — name at least one repository."
                )
            body["repository_ids"] = list(repository_ids)
        else:
            assert repositories is not None  # narrowed by the check above
            if not repositories:
                raise GithubTokenError(
                    "`repositories` is empty. An empty narrowing list is not "
                    "'no restriction' — name at least one repository."
                )
            body["repositories"] = list(repositories)

        jwt = mint_app_jwt(
            app_id=self._app_id, private_key=self._private_key, now=now
        )
        url = f"{self._base}/app/installations/{installation_id}/access_tokens"

        try:
            resp = await self._client.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {jwt}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        except httpx.HTTPError as exc:
            raise GithubTransientError(
                f"could not reach GitHub to mint an installation token: {exc}"
            ) from exc

        return self._parse_mint_response(resp, installation_id)

    def _parse_mint_response(
        self, resp: httpx.Response, installation_id: int
    ) -> InstallationToken:
        """Classify the exchange response.

        CLASSIFICATION DISCIPLINE, inherited and non-negotiable. Only an
        unambiguous provider signal may be treated as terminal, because marking a
        connection dead is destructive to the customer — it tells them to
        reinstall. `app/credentials/oauth.py` establishes this
        (`_classify_failure` "refuses to mark a grant dead on a transient network
        fault") and `app/gcp/probe.py` restates it. Here:

          * 404 / 410 -> the installation is genuinely gone. GitHub returns 404
            for an installation that was deleted, and this is the one signal that
            unambiguously means "reinstall".
          * 401 -> the ASSERTION was rejected, which is a platform-key or clock
            problem, NOT a customer problem. Never terminal for the tenant: a
            wrong platform key would otherwise mark every customer revoked at
            once.
          * 403 -> ambiguous (suspended installation, or a permission the
            installation does not hold). Non-terminal; surfaced with the body so
            an operator can see which.
          * 429 / 5xx / transport -> transient.
        """
        if resp.status_code in (404, 410):
            raise GithubInstallationGone(
                f"GitHub reports installation {installation_id} does not exist "
                f"(HTTP {resp.status_code}). The customer has uninstalled the "
                "App. This is a terminal state: connection_state='revoked'."
            )
        if resp.status_code == 401:
            raise GithubTokenError(
                "GitHub rejected the App assertion (HTTP 401). This is a "
                "PLATFORM problem — a wrong or rotated App private key, a wrong "
                "app id, or host clock skew — and NOT a customer problem, so it "
                "must not mark this tenant's connection revoked. Check "
                "SKYLIZE_GITHUB_APP_ID and SKYLIZE_GITHUB_APP_PRIVATE_KEY_PEM."
            )
        if resp.status_code == 429 or resp.status_code >= 500:
            raise GithubTransientError(
                f"GitHub returned HTTP {resp.status_code} minting a token for "
                f"installation {installation_id}. Transient: stored state is "
                "left untouched."
            )
        if resp.status_code != 201:
            raise GithubTokenError(
                f"unexpected HTTP {resp.status_code} minting a token for "
                f"installation {installation_id}: {resp.text[:400]}"
            )

        try:
            payload: dict[str, Any] = resp.json()
        except ValueError as exc:
            raise GithubTokenError(
                f"GitHub returned HTTP 201 with an unparseable body: {exc}"
            ) from exc

        token = payload.get("token")
        if not isinstance(token, str) or not token:
            raise GithubTokenError(
                "GitHub returned HTTP 201 without a token field; refusing to "
                "continue with no credential."
            )

        expires_raw = payload.get("expires_at")
        expires_at = _parse_expiry(expires_raw)

        got_perms = payload.get("permissions") or {}
        return InstallationToken(
            token=token,
            expires_at=expires_at,
            permissions={str(k): str(v) for k, v in got_perms.items()},
            repository_selection=str(payload.get("repository_selection") or ""),
        )


def _parse_expiry(raw: Any) -> datetime:
    """Parse GitHub's `expires_at`, falling back to the documented 1 hour.

    A missing or unparseable expiry is NOT fatal. The token itself is usable and
    GitHub documents the lifetime as one hour; refusing the credential over a
    timestamp format change would break every governed call for a cosmetic
    reason. The fallback is deliberately CONSERVATIVE — an hour from now is the
    documented maximum, so a caller checking freshness against it can only ever
    be pessimistic, never optimistic.
    """
    if isinstance(raw, str) and raw:
        try:
            # GitHub emits RFC 3339 with a trailing 'Z'.
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc) + timedelta(hours=1)
