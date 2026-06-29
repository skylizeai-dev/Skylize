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
from .rate_limit import RateLimiter
from .routes import api_keys, kill_switch, knowledge, tenants, workflows


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.container = await build_container(settings)
    app.state.rate_limiter = RateLimiter(settings.rate_limit_per_minute)
    try:
        yield
    finally:
        await app.state.container.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="Skylize Gateway", version="0.1.0", lifespan=lifespan)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "backend": get_settings().backend}

    app.include_router(tenants.router)
    app.include_router(api_keys.router)
    app.include_router(workflows.router)
    app.include_router(kill_switch.router)
    app.include_router(knowledge.router)
    return app


app = create_app()
