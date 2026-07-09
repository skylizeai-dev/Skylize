"""
Edge authentication → `RequestContext` (security_architecture.md §3).

Two modes (config-driven):
  - dev_auth: trust `X-Dev-Org` / `X-Dev-User` / `X-Dev-Roles` headers (local).
  - production: verify the OIDC JWT (Bearer) against the IdP JWKS and derive a
    short-lived signed RequestContext.

Internal services trust the RequestContext, never the raw IdP token. TTL is set
here and enforced downstream.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from ..config import Settings
from ..schemas.base import RequestContext


class AuthError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=401, detail=detail)


def _ctx(org_id: str, user_id: str, roles: list[str], ttl_s: int) -> RequestContext:
    now = datetime.now(timezone.utc)
    return RequestContext(
        org_id=org_id, user_id=user_id, roles=roles,
        issued_at=now, expires_at=now + timedelta(seconds=ttl_s),
    )


async def build_request_context(request: Request, settings: Settings) -> RequestContext:
    ttl = settings.request_context_ttl_seconds

    if settings.dev_auth:
        # Dev auth trusts X-Dev-* headers. Fail closed when NONE is presented, so
        # a fully unauthenticated request is denied (401) rather than silently
        # granted a default owner context; sensible defaults still fill any
        # individual header omitted once at least one is present.
        dev_org = request.headers.get("X-Dev-Org")
        dev_user = request.headers.get("X-Dev-User")
        dev_roles = request.headers.get("X-Dev-Roles")
        if dev_org is None and dev_user is None and dev_roles is None:
            raise AuthError("missing authentication")
        roles = [r.strip() for r in (dev_roles or "owner").split(",") if r.strip()]
        return _ctx(dev_org or "org_dev", dev_user or "user_dev", roles, ttl)

    # Production: verify the OIDC JWT.
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise AuthError("missing bearer token")
    token = auth.removeprefix("Bearer ").strip()
    try:
        from jose import jwt  # imported lazily; only the production path needs it

        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            jwks = (await client.get(settings.oidc_jwks_url)).json()
        claims = jwt.decode(
            token, jwks, audience=settings.oidc_audience or None,
            options={"verify_aud": bool(settings.oidc_audience)},
        )
    except Exception as exc:  # noqa: BLE001
        raise AuthError(f"token verification failed: {exc}") from exc

    org_id = claims.get("org_id") or claims.get("org")
    user_id = claims.get("sub")
    if not org_id or not user_id:
        raise AuthError("token missing org_id/sub")
    roles = claims.get("roles", []) or []
    return _ctx(org_id, user_id, roles, ttl)
