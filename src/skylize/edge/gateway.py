"""
The API Gateway (FastAPI) — the Edge Boundary.

Lifespan builds the service container (composition root) and the rate limiter,
then mounts the routes. No business logic lives here: the gateway authenticates,
throttles, and forwards into the Orchestrator / Governance Authority.

Run locally (memory backend, no infra):
    uvicorn skylize.edge.gateway:app --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ..bootstrap import build_container
from ..config import get_settings
from .errors import install_error_handlers
from .rate_limit import RateLimiter
from .routes import (
    agent_prompts,
    agents,
    api_keys,
    audit,
    auth,
    brief,
    credentials,
    deliverables,
    hitl,
    kill_switch,
    knowledge,
    spend,
    tenants,
    workflows,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.container = await build_container(settings)
    app.state.rate_limiter = RateLimiter(settings.rate_limit_per_minute)
    app.state.credential_resolve_limiter = RateLimiter(
        settings.credential_resolve_rate_per_minute
    )
    try:
        yield
    finally:
        await app.state.container.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="Skylize Gateway", version="0.1.0", lifespan=lifespan)

    # Adds `code` alongside `detail` for the raise sites that carry one. Plain
    # HTTPExceptions keep FastAPI's default {"detail": ...} shape untouched.
    install_error_handlers(app)

    settings = get_settings()
    if settings.cors_origins:
        from fastapi.middleware.cors import CORSMiddleware

        # Explicit allow-list only (settings validates against "*"): the
        # gateway sets credentials, so origins must be enumerated.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "backend": get_settings().backend}

    app.include_router(tenants.router)
    app.include_router(api_keys.router)
    app.include_router(auth.router)
    app.include_router(agents.router)
    app.include_router(agent_prompts.router)
    app.include_router(credentials.router)
    app.include_router(deliverables.router)
    app.include_router(hitl.router)
    app.include_router(workflows.router)
    app.include_router(kill_switch.router)
    app.include_router(knowledge.router)
    app.include_router(audit.router)
    app.include_router(spend.router)
    app.include_router(brief.router)
    return app


app = create_app()
