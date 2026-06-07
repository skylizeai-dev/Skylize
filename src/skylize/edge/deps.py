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


def _extract_api_key(request: Request) -> str | None:
    """Pull a presented API key from `X-API-Key` or `Authorization: ApiKey <key>`."""
    header = request.headers.get("X-API-Key")
    if header:
        return header.strip()
    auth = request.headers.get("Authorization", "")
    if auth.startswith("ApiKey "):
        return auth.removeprefix("ApiKey ").strip()
    return None


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


async def enforce_rate_limit(
    ctx: RequestContext = Depends(get_context),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> RequestContext:
    if not limiter.allow(ctx.org_id):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    return ctx


def require_role(role: str) -> Callable[..., Awaitable[RequestContext]]:
    async def _checker(ctx: RequestContext = Depends(get_context)) -> RequestContext:
        if role not in ctx.roles:
            raise HTTPException(status_code=403, detail=f"requires role: {role}")
        return ctx

    return _checker


def require_any_role(*roles: str) -> Callable[..., Awaitable[RequestContext]]:
    """Authorize if the context carries at least one of `roles`."""
    allowed = frozenset(roles)

    async def _checker(ctx: RequestContext = Depends(get_context)) -> RequestContext:
        if allowed.isdisjoint(ctx.roles):
            raise HTTPException(
                status_code=403, detail=f"requires one of roles: {sorted(allowed)}"
            )
        return ctx

    return _checker
