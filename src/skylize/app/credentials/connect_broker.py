"""Generic, provider-registry-driven OAuth connect broker (owner ruling OD5).

OD5 (docs/decisions/2026-11-07_owner_rulings.md:37): "Tenant OAuth connect: one
generic, provider-registry-driven authorize/callback broker." The OAuth design
assigns exactly this to the shared infrastructure, not to any connector:
"the authorize/callback endpoints and `state`/PKCE handling; the token exchange;
encrypted persistence" (docs/06_integrations/oauth_provider_infrastructure_design.md
§5). `oauth.py` already owns freshness/refresh/revocation for a grant that
EXISTS; this module owns how a grant comes to exist.

WHAT THIS MODULE IS, AND IS NOT
- It is the protocol core: build the consent URL (`begin`) and turn the
  provider's callback into a stored, encrypted grant (`complete`).
- It is NOT mounted on any HTTP route. A new `edge/routes/*.py` module becomes
  a Console-served permission-matrix row (edge/permission_matrix.py), the
  durable home of pending state (Redis vs a new table) and the public redirect
  base URL are owner decisions with no ruling yet. Those are a separate,
  serialized step; nothing here pretends they are done.
- It never reads an environment variable. Provider client credentials arrive in
  each spec's `OAuthProviderConfig` from the composition root, exactly as
  `oauth.py` takes them.

SECURITY PROPERTIES (each is pinned by tests/unit/test_connect_broker.py)
1. `state` is 256 bits from `secrets`, SINGLE-USE, and expires after `ttl`.
   It is consumed BEFORE any other check, so a callback that fails for any
   reason cannot be replayed.
2. The store is keyed by SHA-256(state), never the raw value: a dump of the
   pending store does not hand out live callback capabilities.
3. `state` is bound to the (org_id, principal_id) that started the flow. A
   callback completed under a different session is refused — the login-CSRF
   shape where an attacker's grant is attached to a victim's org.
4. PKCE S256 (RFC 7636) by default; the verifier never leaves the server.
5. The `redirect_uri` sent at exchange time is the one stored at `begin`, never
   a value taken from the callback request (RFC 6749 §4.1.3 requires them equal).
6. The authorization-code exchange is attempted ONCE. Codes are single-use, so
   a retry after a timeout cannot succeed and the first attempt's outcome is
   unknown; the caller restarts the flow instead (same rule as OD4).
7. No token, code, state, verifier or client secret is logged or audited.
8. A reconnect that names a DIFFERENT upstream account than the stored grant is
   refused rather than silently swapping whose data the org's agents act on.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

import httpx
import structlog

from ...dal.oauth_credentials import OAuthCredentialRepository, OAuthCredentialRow
from ..audit.service import AuditService
from .encryption import FernetEncryptor
from .oauth import (
    PLATFORM_FERNET_KEY_ID,
    OAuthError,
    OAuthProviderConfig,
    RefreshUnavailable,
    TokenResponse,
)

log = structlog.get_logger()

#: How long a started connect flow stays completable. Long enough for a human to
#: read a consent screen and sign in; short enough that a leaked consent URL
#: goes stale quickly.
DEFAULT_CONNECT_TTL = timedelta(minutes=10)

#: Authorize-URL parameters the broker owns. A spec's `extra_authorize_params`
#: may ADD parameters (Google's access_type=offline, prompt=consent) but can
#: never replace one of these: they carry the security properties above.
_RESERVED_AUTHORIZE_PARAMS = frozenset(
    {
        "response_type",
        "client_id",
        "redirect_uri",
        "scope",
        "state",
        "code_challenge",
        "code_challenge_method",
    }
)

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

#: RFC 6749 section 5.2 error codes are short ASCII tokens (invalid_grant, ...).
_ERROR_CODE_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ConnectError(OAuthError):
    """Base for connect-flow failures. Nothing is stored when one is raised."""


class ConnectProviderUnknown(ConnectError):
    """No `ConnectProviderSpec` is registered under the requested provider."""


class ConnectStateInvalid(ConnectError):
    """The callback's `state` is unknown, expired, already used, or was started
    by a different (org, principal). Deliberately one type with a generic
    message to the caller: telling an attacker WHICH check failed helps them."""


class ConnectExchangeFailed(ConnectError):
    """The provider did not return a usable grant for the authorization code.

    `kind` is a fixed vocabulary (outcome_unknown | provider_rejected |
    unusable_response | missing_code | concurrent_connect | vanished) so callers
    and the audit trail can branch on it without parsing the message.
    """

    def __init__(self, message: str, *, kind: str = "provider_rejected") -> None:
        super().__init__(message)
        self.kind = kind


class ConnectAccountMismatch(ConnectError):
    """A reconnect named a different upstream account than the stored grant."""


# ---------------------------------------------------------------------------
# Registry entry
# ---------------------------------------------------------------------------

def _default_account_id_of(payload: dict[str, Any]) -> str:
    """Name the connected upstream account from the raw token response.

    Providers differ (design doc §5, "account_id_of"; gap 6). This default reads
    the common top-level keys and otherwise returns "" — "not named by this
    provider's token response", never a guess. A provider that can do better
    supplies its own hook.
    """
    for key in ("account_id", "workspace_id", "user_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
        # Numeric ids (common upstream) are names too; bool is an int subclass, not an id.
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return ""


@dataclass(frozen=True, slots=True)
class ConnectProviderSpec:
    """Everything the broker needs for one provider; a registry entry, not code.

    `config` is the SAME `OAuthProviderConfig` the refresh path uses, so the
    token endpoint, platform client credentials, scopes, response parser and
    credential presentation (`auth_style`) are declared once per provider.
    """

    config: OAuthProviderConfig
    authorize_url: str
    #: RFC 7636 S256. On by default; turn off only for a provider documented
    #: to reject the PKCE parameters.
    use_pkce: bool = True
    account_id_of: Callable[[dict[str, Any]], str] = _default_account_id_of
    extra_authorize_params: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        clash = _RESERVED_AUTHORIZE_PARAMS.intersection(self.extra_authorize_params)
        if clash:
            raise ValueError(
                f"extra_authorize_params may not set broker-owned parameters: {sorted(clash)}"
            )
        for name, url in (("authorize_url", self.authorize_url), ("token_url", self.config.token_url)):
            parts = urlsplit(url)
            if parts.scheme != "https" or not parts.netloc:
                # token_url carries the client secret and the code: never plaintext.
                raise ValueError(f"{name} must be an absolute https URL")


# ---------------------------------------------------------------------------
# Pending-state store
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PendingConnect:
    org_id: str
    principal_id: str
    provider: str
    label: str
    redirect_uri: str
    code_verifier: str | None
    created_at: datetime
    expires_at: datetime


class ConnectStateStore(Protocol):
    """Where a started flow waits for its callback.

    `take` MUST be an atomic read-and-delete: two concurrent callbacks carrying
    the same state must not both receive the record. A durable implementation
    (Redis GETDEL, or DELETE ... RETURNING) is an owner decision; only the
    in-memory twin ships here.
    """

    async def put(self, key: str, pending: PendingConnect) -> None: ...

    async def take(self, key: str) -> PendingConnect | None: ...


class InMemoryConnectStateStore:
    """Single-process store for tests and the memory backend.

    A lock makes `take` atomic across concurrent tasks. It holds nothing
    durable: a restart drops every in-flight connect, which only costs the user
    a retry.
    """

    def __init__(self) -> None:
        self._items: dict[str, PendingConnect] = {}
        self._lock = asyncio.Lock()

    async def put(self, key: str, pending: PendingConnect) -> None:
        async with self._lock:
            self._items[key] = pending

    async def take(self, key: str) -> PendingConnect | None:
        async with self._lock:
            return self._items.pop(key, None)

    def __len__(self) -> int:
        return len(self._items)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def state_key(state: str) -> str:
    """The store key for a state value: its SHA-256 hex digest."""
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def pkce_challenge(verifier: str) -> str:
    """RFC 7636 §4.2 S256: BASE64URL(SHA256(ASCII(verifier))), no padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _validate_redirect_uri(redirect_uri: str) -> None:
    parts = urlsplit(redirect_uri)
    if not parts.netloc or parts.fragment:
        raise ValueError("redirect_uri must be an absolute URL without a fragment")
    if parts.scheme == "https":
        return
    if parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS:
        return  # RFC 8252 §7.3 loopback, for local development only
    raise ValueError("redirect_uri must use https (http is allowed only for loopback hosts)")


def build_code_exchange_request(
    config: OAuthProviderConfig,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str | None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build the authorization-code exchange `(form_body, headers)`.

    Client credentials are presented exactly as `oauth.py::_build_token_request`
    presents them for a refresh, keyed by the same `auth_style` field: in the
    form body ('body') or as HTTP Basic with the body left free of them
    ('header'; RFC 6749 §2.3.1 forbids sending both).
    """
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    if code_verifier is not None:
        data["code_verifier"] = code_verifier
    headers: dict[str, str] = {}
    if config.auth_style == "header":
        raw = f"{config.client_id}:{config.client_secret}".encode("utf-8")
        headers["Authorization"] = f"Basic {base64.b64encode(raw).decode('ascii')}"
    else:
        data["client_id"] = config.client_id
        data["client_secret"] = config.client_secret
    # Body-only escape hatch, applied last but never over the protocol fields.
    for key, value in config.extra_token_params.items():
        data.setdefault(key, value)
    return data, headers


@dataclass(frozen=True, slots=True)
class AuthorizeRedirect:
    """Where to send the user's browser. `state` is returned so a caller can
    bind it to the browser session as well (e.g. a short-lived cookie)."""

    url: str
    state: str
    expires_at: datetime


# ---------------------------------------------------------------------------
# The broker
# ---------------------------------------------------------------------------

class ConnectBroker:
    """Starts and completes a tenant's OAuth connect for any registered provider."""

    def __init__(
        self,
        *,
        providers: Mapping[str, ConnectProviderSpec],
        state_store: ConnectStateStore,
        encryptor: FernetEncryptor,
        repo: OAuthCredentialRepository,
        audit: AuditService,
        ttl: timedelta = DEFAULT_CONNECT_TTL,
        key_id: str = PLATFORM_FERNET_KEY_ID,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        token_fn: Callable[[int], str] = secrets.token_urlsafe,
    ) -> None:
        for name, spec in providers.items():
            if spec.config.provider != name:
                raise ValueError(
                    f"registry key {name!r} does not match its config.provider "
                    f"{spec.config.provider!r}"
                )
        self._providers = dict(providers)
        self._store = state_store
        self._enc = encryptor
        self._repo = repo
        self._audit = audit
        self._ttl = ttl
        self._key_id = key_id
        self._http_client_factory = http_client_factory
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._token = token_fn

    def providers(self) -> list[str]:
        return sorted(self._providers)

    def _spec(self, provider: str) -> ConnectProviderSpec:
        spec = self._providers.get(provider)
        if spec is None:
            raise ConnectProviderUnknown(f"no connect spec registered for {provider!r}")
        return spec

    # -- step 1 -------------------------------------------------------------
    async def begin(
        self,
        *,
        org_id: str,
        principal_id: str,
        provider: str,
        redirect_uri: str,
        label: str = "",
        correlation_id: UUID | None = None,
    ) -> AuthorizeRedirect:
        """Record a pending connect and return the provider consent URL."""
        if not org_id or not principal_id:
            raise ValueError("org_id and principal_id are required")
        spec = self._spec(provider)
        _validate_redirect_uri(redirect_uri)

        state = self._token(32)  # 32 random bytes -> 43 url-safe chars
        verifier = self._token(64) if spec.use_pkce else None  # 86 chars; RFC 7636 allows 43-128
        now = self._now()
        pending = PendingConnect(
            org_id=org_id,
            principal_id=principal_id,
            provider=provider,
            label=label,
            redirect_uri=redirect_uri,
            code_verifier=verifier,
            created_at=now,
            expires_at=now + self._ttl,
        )
        await self._store.put(state_key(state), pending)

        params: dict[str, str] = dict(spec.extra_authorize_params)
        params.update(
            {
                "response_type": "code",
                "client_id": spec.config.client_id,
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )
        if spec.config.scopes:
            params["scope"] = " ".join(spec.config.scopes)
        if verifier is not None:
            params["code_challenge"] = pkce_challenge(verifier)
            params["code_challenge_method"] = "S256"
        sep = "&" if "?" in spec.authorize_url else "?"
        url = f"{spec.authorize_url}{sep}{urlencode(params)}"

        await self._audit.record(
            org_id=org_id,
            correlation_id=correlation_id or uuid4(),
            action_type="oauth.connect_started",
            result="success",
            inputs={"provider": provider, "label": label, "principal_id": principal_id},
        )
        return AuthorizeRedirect(url=url, state=state, expires_at=pending.expires_at)

    # -- step 2 -------------------------------------------------------------
    async def complete(
        self,
        *,
        state: str,
        code: str,
        org_id: str,
        principal_id: str,
        correlation_id: UUID | None = None,
    ) -> OAuthCredentialRow:
        """Exchange the callback's code and persist the encrypted grant.

        `org_id`/`principal_id` are the CALLER's authenticated session, not
        values read from the callback query string.
        """
        corr = correlation_id or uuid4()
        # Consumed first: whatever happens next, this state is spent.
        pending = await self._store.take(state_key(state)) if state else None
        if pending is None:
            await self._deny(org_id, corr, None, "state unknown or already used")
            raise ConnectStateInvalid("connect state is invalid, expired or already used")
        # Binding is checked BEFORE expiry so a foreign session presenting a
        # stale state is still recorded as a binding failure under the org that
        # started the flow, and never under the caller's org.
        if pending.org_id != org_id or pending.principal_id != principal_id:
            # Audited under the org that STARTED the flow: that is the tenant
            # whose connect was being hijacked.
            await self._deny(pending.org_id, corr, pending, "state bound to a different session")
            raise ConnectStateInvalid("connect state is invalid, expired or already used")
        if self._now() >= pending.expires_at:
            await self._deny(org_id, corr, pending, "state expired")
            raise ConnectStateInvalid("connect state is invalid, expired or already used")
        if not code:
            await self._deny(org_id, corr, pending, "callback carried no code")
            raise ConnectExchangeFailed(
                "callback carried no authorization code", kind="missing_code"
            )

        spec = self._spec(pending.provider)
        try:
            payload, token = await self._exchange(spec, pending, code)
        except ConnectExchangeFailed as exc:
            # Fixed reason text: provider-controlled strings never reach the audit row.
            await self._deny(org_id, corr, pending, f"code exchange failed ({exc.kind})")
            raise
        account_id = spec.account_id_of(payload)
        now = self._now()
        expires_at = (
            now + timedelta(seconds=token.expires_in_seconds)
            if token.expires_in_seconds is not None
            else None
        )
        enc_access = self._enc.encrypt(token.access_token)
        enc_refresh = (
            self._enc.encrypt(token.refresh_token) if token.refresh_token is not None else None
        )

        existing = await self._repo.get(org_id, pending.provider, pending.label)
        if existing is not None:
            # Fail closed: an unnamed account on EITHER side cannot be proven to
            # be the same account, so it is treated as a mismatch, never a match.
            if (
                not account_id
                or not existing.provider_account_id
                or existing.provider_account_id != account_id
            ):
                await self._deny(
                    org_id, corr, pending, "reconnect cannot prove the same upstream account"
                )
                raise ConnectAccountMismatch(
                    f"{pending.provider!r} connection {pending.label!r} cannot be proven to name "
                    "the same upstream account; disconnect it before connecting again"
                )
            if enc_refresh is None:
                # Same rule as the refresh path (oauth.py "KEEP the existing one"):
                # a response without refresh_token must not erase the stored one.
                enc_refresh = existing.encrypted_refresh_token
            async with self._repo.tenant_session(org_id) as conn:  # type: ignore[attr-defined]
                updated = await self._repo.update_tokens(
                    conn=conn,
                    cred_id=existing.cred_id,
                    org_id=org_id,
                    encrypted_access_token=enc_access,
                    encrypted_refresh_token=enc_refresh,
                    expires_at=expires_at,
                    key_id=self._key_id,
                    refreshed_at=now,
                )
            if not updated:
                raise ConnectExchangeFailed(
                    "stored grant vanished during reconnect", kind="vanished"
                )
            stored = await self._repo.get(org_id, pending.provider, pending.label)
            assert stored is not None
            action = "oauth.connect_reconnected"
        else:
            stored = OAuthCredentialRow(
                cred_id=uuid4(),
                org_id=org_id,
                provider=pending.provider,
                label=pending.label,
                provider_account_id=account_id,
                key_id=self._key_id,
                encrypted_access_token=enc_access,
                encrypted_refresh_token=enc_refresh,
                expires_at=expires_at,
                # Granted scopes when the provider reports them, else what we
                # asked for (RFC 6749 §5.1: scope is OPTIONAL when unchanged).
                scopes=token.scopes or spec.config.scopes,
                connection_state="valid",
                state_reason=None,
                created_at=now,
                updated_at=now,
                refreshed_at=None,
            )
            try:
                await self._repo.insert(stored)
            except Exception as exc:
                # Two first-time connects for one (org, provider, label) can race
                # past the `get` above; the loser's insert hits the unique index.
                # Report it as such instead of leaking a driver error.
                if await self._repo.get(org_id, pending.provider, pending.label) is None:
                    raise
                await self._deny(org_id, corr, pending, "concurrent connect already stored a grant")
                raise ConnectExchangeFailed(
                    "a concurrent connect for this provider and label completed first; "
                    "reconnect to replace it",
                    kind="concurrent_connect",
                ) from exc
            action = "oauth.connect_completed"

        await self._audit.record(
            org_id=org_id,
            correlation_id=corr,
            action_type=action,
            result="success",
            inputs={
                "provider": pending.provider,
                "label": pending.label,
                "principal_id": principal_id,
                "provider_account_id": account_id,
                # Granted by THIS exchange. On a reconnect the stored `scopes`
                # column is not rewritten (update_tokens does not touch it), so
                # both are recorded rather than the stale stored set alone.
                "scopes_granted": list(token.scopes or spec.config.scopes),
                "scopes_stored": list(stored.scopes),
            },
        )
        return stored

    # -- internals ----------------------------------------------------------
    async def _exchange(
        self, spec: ConnectProviderSpec, pending: PendingConnect, code: str
    ) -> tuple[dict[str, Any], TokenResponse]:
        data, headers = build_code_exchange_request(
            spec.config,
            code=code,
            redirect_uri=pending.redirect_uri,
            code_verifier=pending.code_verifier,
        )
        client = (
            self._http_client_factory()
            if self._http_client_factory is not None
            else httpx.AsyncClient(timeout=spec.config.timeout_seconds)
        )
        try:
            async with client:
                resp = await client.post(spec.config.token_url, data=data, headers=headers)
        except httpx.HTTPError as exc:
            log.warning(
                "oauth_connect_exchange_transport_error",
                provider=pending.provider,
                error_type=type(exc).__name__,
            )
            raise ConnectExchangeFailed(
                f"OUTCOME UNKNOWN: the code exchange with {pending.provider!r} did not "
                "complete. Authorization codes are single-use, so it is not retried; "
                "start the connect flow again.",
                kind="outcome_unknown",
            ) from exc

        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            payload = {}
        if resp.status_code != 200:
            # Only the RFC 6749 §5.2 error code is surfaced; the body is never echoed.
            err = payload.get("error")
            # Provider-controlled: echoed only when it looks like an RFC 6749 code.
            err_code = (
                err if isinstance(err, str) and _ERROR_CODE_RE.fullmatch(err) else "unrecognized"
            )
            raise ConnectExchangeFailed(
                f"{pending.provider!r} rejected the code exchange "
                f"(HTTP {resp.status_code}, error={err_code})"
            )
        try:
            token = spec.config.parse_token_response(payload)
        except RefreshUnavailable as exc:
            raise ConnectExchangeFailed(
                f"unusable token response: {exc}", kind="unusable_response"
            ) from exc
        return payload, token

    async def _deny(
        self, org_id: str, corr: UUID, pending: PendingConnect | None, reason: str
    ) -> None:
        if not org_id:
            return
        await self._audit.record(
            org_id=org_id,
            correlation_id=corr,
            action_type="oauth.connect_denied",
            result="denied",
            inputs={
                "provider": pending.provider if pending else None,
                "label": pending.label if pending else None,
            },
            result_reason=reason,
        )
