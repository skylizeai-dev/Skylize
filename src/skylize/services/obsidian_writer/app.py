"""
obsidian_writer FastAPI microservice.

Run:
    uvicorn skylize.services.obsidian_writer.app:app --port 8001 --reload

Required env vars:
    OBSIDIAN_VAULT_ROOT=/path/to/vault
    OBSIDIAN_HMAC_SECRET=<secret>
    OBSIDIAN_REDIS_URL=redis://localhost:6379   (optional, has default)
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from redis.asyncio import Redis, from_url

from .lock import LockAcquisitionError, RedisLockManager
from .settings import ObsidianSettings, get_settings
from .writer import ObsidianWriter, PathTraversalError

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Rate limiter backed by Redis (fixed-window INCR/EXPIRE per org_id)
# ---------------------------------------------------------------------------


class RedisRateLimiter:
    def __init__(self, redis: Redis, per_minute: int) -> None:
        self._redis = redis
        self._limit = per_minute

    async def allow(self, org_id: str) -> bool:
        now = int(time.time())
        window = now - (now % 60)
        key = f"obsidian:rl:{org_id}:{window}"
        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, 90)  # slightly > 60s for safety
        return count <= self._limit


# ---------------------------------------------------------------------------
# Lifespan: build shared resources once
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: ObsidianSettings = get_settings()
    redis: Redis = from_url(settings.redis_url, decode_responses=True)
    lock_mgr = RedisLockManager(redis, ttl_ms=settings.lock_ttl_ms)
    writer = ObsidianWriter(vault_root=settings.vault_root, lock_manager=lock_mgr)
    rate_limiter = RedisRateLimiter(redis, per_minute=settings.rate_limit_per_minute)

    app.state.writer = writer
    app.state.rate_limiter = rate_limiter
    app.state.settings = settings
    app.state.redis = redis

    try:
        yield
    finally:
        await redis.aclose()


# ---------------------------------------------------------------------------
# HMAC verification helper (shared pattern with knowledge.py)
# ---------------------------------------------------------------------------


def _verify_hmac(body: bytes, signature_header: str | None, secret: str) -> bool:
    if not signature_header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


async def _check_auth_and_rate(request: Request, body: bytes) -> None:
    settings: ObsidianSettings = request.app.state.settings
    sig = request.headers.get("X-Hub-Signature-256")
    if not _verify_hmac(body, sig, settings.hmac_secret):
        raise HTTPException(status_code=403, detail="invalid HMAC signature")


async def _check_rate(request: Request, org_id: str) -> None:
    limiter: RedisRateLimiter = request.app.state.rate_limiter
    if not await limiter.allow(org_id):
        raise HTTPException(status_code=429, detail="rate limit exceeded")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ObsidianWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_path: str
    content: str
    org_id: str


class WriteResponse(BaseModel):
    status: str
    file_path: str
    bytes_written: int


# ---------------------------------------------------------------------------
# App + routes
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(title="obsidian_writer", version="0.1.0", lifespan=lifespan)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "obsidian_writer"}

    @app.post("/write", response_model=WriteResponse, tags=["write"])
    async def write_file(request: Request, body: ObsidianWriteRequest) -> WriteResponse:
        raw = await request.body()
        await _check_auth_and_rate(request, raw)
        await _check_rate(request, body.org_id)

        writer: ObsidianWriter = request.app.state.writer
        try:
            result = await writer.write(body.file_path, body.content, body.org_id)
        except PathTraversalError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except LockAcquisitionError as exc:
            raise HTTPException(status_code=503, detail=str(exc))

        return WriteResponse(status="ok", file_path=result.file_path, bytes_written=result.bytes_written)

    @app.post("/append", response_model=WriteResponse, tags=["write"])
    async def append_file(request: Request, body: ObsidianWriteRequest) -> WriteResponse:
        raw = await request.body()
        await _check_auth_and_rate(request, raw)
        await _check_rate(request, body.org_id)

        writer: ObsidianWriter = request.app.state.writer
        try:
            result = await writer.append(body.file_path, body.content, body.org_id)
        except PathTraversalError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except LockAcquisitionError as exc:
            raise HTTPException(status_code=503, detail=str(exc))

        return WriteResponse(status="ok", file_path=result.file_path, bytes_written=result.bytes_written)

    return app


app = create_app()
