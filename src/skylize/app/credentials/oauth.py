"""Provider-agnostic OAuth grant freshness + on-demand refresh.

Design: docs/06_integrations/oauth_provider_infrastructure_design.md §2, §4, §5.
Nothing here is Drive-specific. A provider contributes an ``OAuthProviderConfig``
— endpoint, client credentials, and two small parsing hooks — and this module
owns storage, encryption, freshness, concurrency, revocation state, and audit.

WHY ON-DEMAND AND NOT A BACKGROUND SWEEP (design §2.2). A refresh is a WRITE.
Migration 0002 granted the cross-tenant carve-out for READS only and says so
explicitly: "WRITE (WITH CHECK) is UNCHANGED — still requires a matching org_id,
so the rehydrate flag can never be used to write across tenants" (0002:11-16).
A cross-tenant refresher would therefore need a write carve-out (reversing that
decision) or a superuser connection (dissolving tenant isolation on the table
holding every customer's third-party tokens). Refreshing inside
``tenant_session(org_id)`` on the request path needs neither: the org is already
bound, and the write is a same-tenant write.

ENCRYPTION reuses ``FernetEncryptor`` and the one platform key resolved at
bootstrap (``resolve_credential_encryption_key``). No second scheme is
introduced. ``key_id`` records which key produced a row's ciphertext so a future
rotation does not require rewriting existing rows.

THE CENTRAL SAFETY RULE: marking a grant 'revoked' is destructive to the
customer — it demands they reconnect — so ONLY an unambiguous provider signal
may do it. A timeout, a 500, or a 429 denies the call and leaves the stored state
untouched. See ``_classify_failure``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable
from uuid import UUID, uuid4

import httpx
import structlog

from ...dal.oauth_credentials import (
    OAuthCredentialRepository,
    OAuthCredentialRow,
)
from ..audit.service import AuditService
from .encryption import FernetEncryptor

log = structlog.get_logger()

#: Refresh this far ahead of the recorded expiry. Never trust an access token to
#: its last second: the provider's clock, ours, and the network are all
#: independent sources of skew, and the cost of refreshing early is one extra
#: round trip while the cost of refreshing late is a failed customer action.
DEFAULT_EXPIRY_SKEW = timedelta(minutes=5)

#: Identifies the single platform-wide Fernet key from bootstrap. Stored on every
#: row so a later rotation can tell old ciphertext from new without a backfill.
PLATFORM_FERNET_KEY_ID = "platform-fernet-v1"


class GrantStatus(str, Enum):
    """Result of evaluating a stored grant, before any refresh is attempted."""

    VALID = "valid"                  # usable now
    NEEDS_REFRESH = "needs_refresh"  # past (expiry - skew) but repairable
    DEAD = "dead"                    # unusable without a human reconnect


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class OAuthError(Exception):
    """Base for grant-resolution failures."""


class GrantNotConnected(OAuthError):
    """No grant row exists for this (org, provider, label)."""


class GrantRevoked(OAuthError):
    """The grant is dead and a human must reconnect.

    Raised both when the stored state is already terminal and when a refresh
    attempt returns an unambiguous provider revocation signal. Distinct from
    `RefreshUnavailable` on purpose: this one is a durable fact about the
    customer's connection, not an operational blip.
    """


class RefreshUnavailable(OAuthError):
    """The grant could not be evaluated or refreshed for a TRANSIENT reason.

    Its own type mirroring `ToolSpendUnavailable`: "we could not check" must
    never collapse into "there was nothing to find". Both deny the call; only
    this one is an operational fault worth alerting on, and — critically — it
    must NOT mark the customer's grant revoked.
    """


# ---------------------------------------------------------------------------
# Provider configuration — the entire provider-variable surface
# ---------------------------------------------------------------------------

def _default_parse_token_response(payload: dict[str, Any]) -> "TokenResponse":
    """RFC 6749 §5.1 token response.

    ``refresh_token`` is optional on a refresh response: providers that do not
    rotate refresh tokens simply omit it, and the caller must then KEEP the
    existing one rather than overwrite it with None.
    """
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise RefreshUnavailable("token response carried no usable access_token")
    expires_in = payload.get("expires_in")
    if not isinstance(expires_in, (int, float)) or isinstance(expires_in, bool):
        raise RefreshUnavailable(
            f"token response carried a non-numeric expires_in: {expires_in!r}"
        )
    scope = payload.get("scope")
    scopes = tuple(scope.split()) if isinstance(scope, str) and scope else ()
    new_refresh = payload.get("refresh_token")
    return TokenResponse(
        access_token=access_token,
        expires_in_seconds=int(expires_in),
        refresh_token=new_refresh if isinstance(new_refresh, str) and new_refresh else None,
        scopes=scopes,
    )


def _default_is_revocation(status_code: int, payload: dict[str, Any]) -> bool:
    """RFC 6749 §5.2: ``invalid_grant`` means the grant is dead.

    Deliberately narrow. A 400 alone is NOT enough — ``invalid_request`` and
    ``invalid_scope`` are also 400s and are OUR bugs, not the customer's revoked
    connection. ``invalid_client`` is a platform misconfiguration (our client id
    or secret is wrong) and must never be reported as the customer revoking us.
    """
    return status_code in (400, 401) and payload.get("error") == "invalid_grant"


@dataclass(frozen=True, slots=True)
class TokenResponse:
    access_token: str
    expires_in_seconds: int
    refresh_token: str | None
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OAuthProviderConfig:
    """Everything a provider contributes. Nothing here is Drive-specific.

    ``client_id``/``client_secret`` are PLATFORM-level values: Skylize registers
    one OAuth application per provider and customers authorize into it. They are
    passed in from ``Settings`` by the composition root, exactly as every other
    platform secret in this repo is (config.py:128-160) — this module never reads
    an environment variable itself.
    """

    provider: str
    token_url: str
    client_id: str
    client_secret: str
    scopes: tuple[str, ...] = ()
    #: Provider response shapes differ (absolute vs relative expiry, scope
    #: encoding). These two hooks are the ONLY places that variation may leak in.
    parse_token_response: Callable[[dict[str, Any]], TokenResponse] = (
        _default_parse_token_response
    )
    is_revocation_error: Callable[[int, dict[str, Any]], bool] = _default_is_revocation
    timeout_seconds: float = 10.0
    extra_token_params: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pure status evaluation
# ---------------------------------------------------------------------------

def evaluate_grant(
    row: OAuthCredentialRow,
    *,
    now: datetime,
    skew: timedelta = DEFAULT_EXPIRY_SKEW,
) -> GrantStatus:
    """Classify a stored grant. Pure: no I/O, no mutation, fully unit-testable.

    Ordering matters. A terminal stored state wins over the timestamp: a revoked
    grant whose ``expires_at`` is still in the future is DEAD, not VALID — the
    provider's verdict outranks our cached clock.
    """
    if row.connection_state in ("revoked", "expired"):
        return GrantStatus.DEAD
    if row.expires_at > now + skew:
        return GrantStatus.VALID
    # Past the skew window. Repairable only if we hold a refresh token.
    if not row.encrypted_refresh_token:
        return GrantStatus.DEAD
    return GrantStatus.NEEDS_REFRESH


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------

class OAuthCredentialService:
    """Resolves a live access token for (org, provider, label), refreshing if needed.

    Registered provider configs are supplied at construction, so a connector is a
    configuration entry rather than a code path through this module.
    """

    def __init__(
        self,
        *,
        encryptor: FernetEncryptor,
        repo: OAuthCredentialRepository,
        audit: AuditService,
        providers: dict[str, OAuthProviderConfig] | None = None,
        skew: timedelta = DEFAULT_EXPIRY_SKEW,
        key_id: str = PLATFORM_FERNET_KEY_ID,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._enc = encryptor
        self._repo = repo
        self._audit = audit
        self._providers = dict(providers or {})
        self._skew = skew
        self._key_id = key_id
        self._http_client_factory = http_client_factory
        self._now = now_fn or (lambda: datetime.now(timezone.utc))

    def register_provider(self, config: OAuthProviderConfig) -> None:
        self._providers[config.provider] = config

    def is_registered(self, provider: str) -> bool:
        return provider in self._providers

    # -- public API ---------------------------------------------------------

    async def ensure_fresh(
        self,
        *,
        org_id: str,
        provider: str,
        label: str = "",
        correlation_id: UUID | None = None,
    ) -> OAuthCredentialRow:
        """Guarantee a usable grant, refreshing on demand. Returns the live row.

        Raises `GrantNotConnected`, `GrantRevoked`, or `RefreshUnavailable` —
        every one of which is a DENIAL. There is no return path that hands back a
        stale or unverified grant.
        """
        row = await self._repo.get(org_id, provider, label)
        if row is None:
            raise GrantNotConnected(
                f"{provider!r} is not connected for org {org_id!r}"
            )

        status = evaluate_grant(row, now=self._now(), skew=self._skew)
        if status is GrantStatus.VALID:
            return row
        if status is GrantStatus.DEAD:
            # Persist the terminal state if the row does not already carry one:
            # this is the "expired and unrepairable" case (no refresh token).
            if row.connection_state == "valid":
                await self._mark_dead(
                    row,
                    state="expired",
                    reason="access token past expiry and no refresh token is stored",
                    correlation_id=correlation_id,
                )
            raise GrantRevoked(
                f"{provider!r} grant for org {org_id!r} is {row.connection_state!r} "
                "and cannot be refreshed; the customer must reconnect"
            )
        return await self._refresh(
            row, provider=provider, label=label, correlation_id=correlation_id
        )

    async def access_token(
        self,
        *,
        org_id: str,
        provider: str,
        label: str = "",
        correlation_id: UUID | None = None,
    ) -> str:
        """Decrypted, guaranteed-fresh access token.

        Return value MUST NOT appear in logs — same contract as
        ``CredentialVault.retrieve`` (app/credentials/vault.py:69).
        """
        row = await self.ensure_fresh(
            org_id=org_id, provider=provider, label=label, correlation_id=correlation_id
        )
        return self._enc.decrypt(row.encrypted_access_token)

    # -- refresh ------------------------------------------------------------

    async def _refresh(
        self,
        row: OAuthCredentialRow,
        *,
        provider: str,
        label: str,
        correlation_id: UUID | None,
    ) -> OAuthCredentialRow:
        config = self._providers.get(provider)
        if config is None:
            # Fail closed: a grant we cannot refresh is not a grant we may use.
            raise RefreshUnavailable(
                f"no OAuthProviderConfig registered for {provider!r}; failing closed"
            )

        # Set when the provider reports the grant dead. The revocation is
        # deliberately persisted AFTER the refresh transaction closes, never
        # inside it: raising out of `tenant_session` rolls the transaction back,
        # which would discard the very state that tells the customer to
        # reconnect. The grant would then look healthy forever and every
        # subsequent call would re-hit the provider's token endpoint.
        # (Regression-tested by
        # tests/integration/test_oauth_credentials_pg.py::
        # test_revocation_writes_queryable_connection_state — the in-memory twin
        # cannot catch this, because it has no transaction to roll back.)
        revoked: tuple[OAuthCredentialRow, str] | None = None

        # ONE transaction across lock -> refresh -> write. `get_for_update` takes
        # a row lock so a concurrent refresh of the same grant serialises behind
        # us instead of racing to persist a rotated refresh token.
        async with self._repo.tenant_session(row.org_id) as conn:  # type: ignore[attr-defined]
            locked = await self._repo.get_for_update(conn, row.org_id, provider, label)
            if locked is None:
                raise GrantNotConnected(
                    f"{provider!r} grant for org {row.org_id!r} vanished during refresh"
                )

            # Re-evaluate under the lock. If we waited behind another refresher
            # that already succeeded, its result is fresh and there is nothing to
            # do — refreshing again would burn a rotated token for no reason.
            status = evaluate_grant(locked, now=self._now(), skew=self._skew)
            if status is GrantStatus.VALID:
                return locked
            if status is GrantStatus.DEAD:
                raise GrantRevoked(
                    f"{provider!r} grant for org {row.org_id!r} is "
                    f"{locked.connection_state!r}; the customer must reconnect"
                )

            assert locked.encrypted_refresh_token is not None  # NEEDS_REFRESH implies this
            refresh_token = self._enc.decrypt(locked.encrypted_refresh_token)

            try:
                token = await self._post_refresh(config, refresh_token)
            except GrantRevoked as exc:
                revoked = (locked, str(exc))
                token = None  # type: ignore[assignment]

            if revoked is None:
                now = self._now()
                new_expiry = now + timedelta(seconds=token.expires_in_seconds)
                # A provider that does not rotate refresh tokens omits the field;
                # keep the existing one rather than nulling out our only way back.
                new_refresh_ct = (
                    self._enc.encrypt(token.refresh_token)
                    if token.refresh_token is not None
                    else locked.encrypted_refresh_token
                )
                await self._repo.update_tokens(
                    conn=conn,
                    cred_id=locked.cred_id,
                    org_id=locked.org_id,
                    encrypted_access_token=self._enc.encrypt(token.access_token),
                    encrypted_refresh_token=new_refresh_ct,
                    expires_at=new_expiry,
                    key_id=self._key_id,
                    refreshed_at=now,
                )
                refreshed = await self._repo.get_for_update(
                    conn, row.org_id, provider, label
                )

        # The refresh transaction has now COMMITTED (or, on the revoked path,
        # closed without writing). Persist the terminal state in its own
        # transaction so it survives the exception raised immediately below.
        if revoked is not None:
            locked_row, reason = revoked
            await self._repo.set_connection_state(
                cred_id=locked_row.cred_id, org_id=locked_row.org_id,
                state="revoked", reason=reason,
            )
            await self._audit_state(
                locked_row, "revoked", reason, correlation_id, conn_bound=False
            )
            log.warning(
                "oauth.grant_revoked",
                org_id=locked_row.org_id, provider=provider, label=label,
            )
            raise GrantRevoked(reason)

        await self._audit.record(
            org_id=row.org_id,
            correlation_id=correlation_id or uuid4(),
            action_type="oauth.refreshed",
            result="success",
            inputs={"provider": provider, "label": label},
        )
        log.info("oauth.refreshed", org_id=row.org_id, provider=provider, label=label)
        if refreshed is None:  # pragma: no cover — the row was just written
            raise RefreshUnavailable("grant disappeared immediately after refresh")
        return refreshed

    async def _post_refresh(
        self, config: OAuthProviderConfig, refresh_token: str
    ) -> TokenResponse:
        """The token-endpoint round trip. Raises GrantRevoked or RefreshUnavailable.

        One client per call, never cached — the HubSpot precedent
        (tools/builtin/hubspot_tools.py:76-83), so no credential can leak across
        orgs through a shared client.
        """
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            **config.extra_token_params,
        }
        client = (
            self._http_client_factory()
            if self._http_client_factory is not None
            else httpx.AsyncClient(timeout=config.timeout_seconds)
        )
        try:
            try:
                response = await client.post(config.token_url, data=data)
            except httpx.HTTPError as exc:
                # Network-level failure. Transient by assumption — NEVER a
                # revocation. Assuming otherwise would let one flaky DNS lookup
                # tell a customer their integration was disconnected.
                raise RefreshUnavailable(
                    f"token endpoint unreachable for {config.provider!r}: {exc}"
                ) from exc
            return self._classify_failure(config, response)
        finally:
            await client.aclose()

    def _classify_failure(
        self, config: OAuthProviderConfig, response: httpx.Response
    ) -> TokenResponse:
        """Turn an HTTP response into a token or the RIGHT kind of error.

        The asymmetry is deliberate and is the security core of this module:
        only an unambiguous provider revocation signal may produce `GrantRevoked`
        (which writes durable state and forces the customer to reconnect).
        Everything else denies the call and leaves the stored grant alone.
        """
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                payload = {}
        except ValueError:
            payload = {}

        if response.status_code == 200:
            return config.parse_token_response(payload)

        if config.is_revocation_error(response.status_code, payload):
            raise GrantRevoked(
                f"provider reported the grant dead "
                f"({payload.get('error', 'invalid_grant')})"
            )

        # invalid_client is OUR misconfiguration, not the customer's revocation.
        # Called out explicitly so it is never silently folded into the branch
        # above by a future edit.
        if payload.get("error") == "invalid_client":
            raise RefreshUnavailable(
                f"platform OAuth client rejected by {config.provider!r} "
                "(invalid_client) — this is a Skylize misconfiguration"
            )

        raise RefreshUnavailable(
            f"token endpoint returned {response.status_code} for "
            f"{config.provider!r}: {payload.get('error', 'unknown error')}"
        )

    # -- state transitions --------------------------------------------------

    async def _mark_dead(
        self,
        row: OAuthCredentialRow,
        *,
        state: str,
        reason: str,
        correlation_id: UUID | None,
    ) -> None:
        await self._repo.set_connection_state(
            cred_id=row.cred_id, org_id=row.org_id,
            state=state, reason=reason,  # type: ignore[arg-type]
        )
        await self._audit_state(row, state, reason, correlation_id, conn_bound=False)

    async def _audit_state(
        self,
        row: OAuthCredentialRow,
        state: str,
        reason: str,
        correlation_id: UUID | None,
        *,
        conn_bound: bool,  # noqa: ARG002 — kept for call-site clarity
    ) -> None:
        await self._audit.record(
            org_id=row.org_id,
            correlation_id=correlation_id or uuid4(),
            action_type="oauth.connection_state_changed",
            result="denied",
            inputs={
                "provider": row.provider,
                "label": row.label,
                "connection_state": state,
            },
            result_reason=reason,
        )
