"""FastAPI dependencies: container, request context, rate-limit enforcement."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, Request

from ..bootstrap import Container
from ..schemas.base import RequestContext
from .auth import build_request_context
from .errors import CodedHTTPException, ErrorCode
from .rate_limit import RateLimiter


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def get_rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


def get_credential_resolve_limiter(request: Request) -> RateLimiter:
    """Per-org limiter for GET /api/v1/credentials/resolve.

    Mirrors get_rate_limiter exactly: the limiter is constructed in the gateway
    lifespan and read from app.state here. The credentials router is NOT mounted
    yet (see docs/audits/epic_user_auth_buildout.md), so this reader exists only
    so credentials.py imports cleanly; populating
    app.state.credential_resolve_limiter is part of that deferred mount work.
    """
    limiter: RateLimiter = request.app.state.credential_resolve_limiter
    return limiter


def _extract_api_key(request: Request) -> str | None:
    """Pull a presented API key from `X-API-Key` or `Authorization: ApiKey <key>`."""
    header = request.headers.get("X-API-Key")
    if header:
        return header.strip()
    auth = request.headers.get("Authorization", "")
    if auth.startswith("ApiKey "):
        return auth.removeprefix("ApiKey ").strip()
    return None


async def get_current_user(
    request: Request, container: Container = Depends(get_container)
) -> RequestContext:
    """Human-user auth path: verify an `Authorization: Bearer <access_jwt>` and
    project its claims into a RequestContext (`user_id`, `org_id`, `roles`).

    Distinct from `get_context` (agent/API-key path). Rejects missing, malformed,
    non-access, or invalid tokens with 401.
    """
    from datetime import datetime, timezone

    from ..app.auth.tokens import InvalidTokenError, decode_token

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = auth.removeprefix("Bearer ").strip()
    try:
        claims = decode_token(token, container.settings.jwt_secret)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="invalid or expired token") from exc
    if claims.get("type") != "access":
        raise HTTPException(status_code=401, detail="not an access token")
    return RequestContext(
        org_id=str(claims.get("org_id", "")),
        user_id=str(claims.get("sub", "")),
        roles=list(claims.get("roles", [])),
        expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc),
    )


async def get_context(
    request: Request, container: Container = Depends(get_container)
) -> RequestContext:
    # Agent-to-agent path: an API key resolves to the same RequestContext shape
    # as an OIDC token, so all downstream RBAC/isolation is credential-agnostic.
    presented = _extract_api_key(request)
    if presented is not None:
        ctx = await container.api_keys.authenticate(
            presented, ttl_s=container.settings.request_context_ttl_seconds
        )
        if ctx is None:
            raise HTTPException(status_code=401, detail="invalid API key")
        return ctx
    return await build_request_context(request, container.settings)


async def get_context_or_user(
    request: Request, container: Container = Depends(get_container)
) -> RequestContext:
    """Combined auth for human-facing (console) routes: accept a Skylize access
    JWT (the `Authorization: Bearer <access_jwt>` the console holds, the same token
    /auth/me validates via get_current_user) first, otherwise fall back to
    get_context (X-Dev-* headers / OIDC / API key). Non-fatal on a missing or
    non-Skylize Bearer, so every existing X-Dev-*/OIDC/API-key caller is unchanged.
    """
    from datetime import datetime, timezone

    from ..app.auth.tokens import InvalidTokenError, decode_token

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth.removeprefix("Bearer ").strip()
        try:
            claims = decode_token(token, container.settings.jwt_secret)
            if claims.get("type") == "access":
                return RequestContext(
                    org_id=str(claims.get("org_id", "")),
                    user_id=str(claims.get("sub", "")),
                    roles=list(claims.get("roles", [])),
                    expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc),
                )
        except InvalidTokenError:
            pass  # not a Skylize JWT (e.g. an OIDC token) — fall through to get_context
    return await get_context(request, container)


async def enforce_rate_limit(
    ctx: RequestContext = Depends(get_context),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> RequestContext:
    if not limiter.allow(ctx.org_id):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    return ctx


async def enforce_rate_limit_or_user(
    ctx: RequestContext = Depends(get_context_or_user),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> RequestContext:
    """Same limiter as `enforce_rate_limit`, resolved through `get_context_or_user`.

    For routes whose OWN `ctx` dependency is `require_any_role_or_user` (i.e. the
    human-facing console routes that accept a Skylize access JWT). Pairing those
    with `enforce_rate_limit` resolves the caller TWICE through two different
    auth paths, and the limiter's `get_context` leg rejects a Skylize JWT with
    401 before the route's own — correct — resolver ever runs.

    This changes no token verification, signature, scope, or audit behaviour: it
    reuses the existing `get_context_or_user`, which itself falls back to
    `get_context` for every X-Dev-*/OIDC/API-key caller. Routes that consume the
    RETURNED context as their RBAC/tenant source keep using `enforce_rate_limit`
    unchanged.
    """
    if not limiter.allow(ctx.org_id):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    return ctx


async def enforce_anonymous_rate_limit(
    request: Request,
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> None:
    """Rate limit a route that runs BEFORE authentication.

    `enforce_rate_limit` cannot be used on `/auth/register|login|refresh`: it
    resolves `get_context` first, so an unauthenticated caller is refused with
    401 before any limiting happens — it would break the very routes that mint
    the first credential. There is no `org_id` to key on at that point, so this
    keys on the peer address instead.

    TWO HONEST LIMITS, both of which need the Redis move (`rate_limit.py:4-5`)
    or a trusted proxy header to close:
      * The peer address is `request.client.host` — deliberately NOT
        X-Forwarded-For, which any caller can set. Behind a load balancer that
        collapses to the balancer's address, i.e. one shared bucket, which is
        strictly more restrictive than intended, never less.
      * The limiter is in-process (`rate_limit.py:16`), so the effective limit
        multiplies by worker and replica count.

    Keys are prefixed so an address can never collide with an `org_id` bucket.
    """
    peer = request.client.host if request.client else "unknown"
    if not limiter.allow(f"anon:{peer}"):
        raise HTTPException(status_code=429, detail="rate limit exceeded")


def require_role(
    role: str, *, resolver: Callable[..., Awaitable[RequestContext]] = get_context
) -> Callable[..., Awaitable[RequestContext]]:
    async def _checker(ctx: RequestContext = Depends(resolver)) -> RequestContext:
        if role not in ctx.roles:
            # AUTHORIZATION_FAILED, not a governance outcome: the presented
            # credential lacks the role, so the handler never ran and nothing
            # about the request's content was evaluated. Same 403 status and
            # same detail string as before; only `code` is new.
            raise CodedHTTPException(
                status_code=403,
                detail=f"requires role: {role}",
                code=ErrorCode.AUTHORIZATION_FAILED,
            )
        return ctx

    return _checker


def require_any_role(
    *roles: str, resolver: Callable[..., Awaitable[RequestContext]] = get_context
) -> Callable[..., Awaitable[RequestContext]]:
    """Authorize if the context carries at least one of `roles`.

    `resolver` selects the auth path: the default get_context covers the
    agent/dev/OIDC/API-key callers; pass get_context_or_user (see
    require_any_role_or_user) to additionally accept a Skylize access JWT.
    """
    allowed = frozenset(roles)

    async def _checker(ctx: RequestContext = Depends(resolver)) -> RequestContext:
        if allowed.isdisjoint(ctx.roles):
            # See require_role: this 403 is about the CALLER, never about the
            # proposal. On /agents/execute it is the case a bare 403 used to
            # make indistinguishable from a decision-engine REJECT.
            raise CodedHTTPException(
                status_code=403,
                detail=f"requires one of roles: {sorted(allowed)}",
                code=ErrorCode.AUTHORIZATION_FAILED,
            )
        return ctx

    return _checker


def require_any_role_or_user(*roles: str) -> Callable[..., Awaitable[RequestContext]]:
    """Like require_any_role, but also accepts a Skylize access JWT (the web
    console's Bearer token) alongside the X-Dev-* / OIDC / API-key paths. Used by
    the human-facing routes the console calls (agents, deliverables)."""
    return require_any_role(*roles, resolver=get_context_or_user)
