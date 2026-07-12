"""FastAPI dependencies: container, request context, rate-limit enforcement."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, Request

from ..bootstrap import Container
from ..schemas.base import RequestContext
from .auth import build_request_context
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


def require_role(
    role: str, *, resolver: Callable[..., Awaitable[RequestContext]] = get_context
) -> Callable[..., Awaitable[RequestContext]]:
    async def _checker(ctx: RequestContext = Depends(resolver)) -> RequestContext:
        if role not in ctx.roles:
            raise HTTPException(status_code=403, detail=f"requires role: {role}")
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
            raise HTTPException(
                status_code=403, detail=f"requires one of roles: {sorted(allowed)}"
            )
        return ctx

    return _checker


def require_any_role_or_user(*roles: str) -> Callable[..., Awaitable[RequestContext]]:
    """Like require_any_role, but also accepts a Skylize access JWT (the web
    console's Bearer token) alongside the X-Dev-* / OIDC / API-key paths. Used by
    the human-facing routes the console calls (agents, deliverables)."""
    return require_any_role(*roles, resolver=get_context_or_user)
