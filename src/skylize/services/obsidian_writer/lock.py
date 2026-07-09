"""RedisLockManager — distributed per-file lock via SET NX PX."""

from __future__ import annotations

import asyncio
import hashlib
import random
import secrets

import structlog
from redis.asyncio import Redis

log = structlog.get_logger(__name__)

_BASE_BACKOFF_MS = 200
_JITTER_MS = 100


class LockAcquisitionError(Exception):
    """Raised when a distributed lock cannot be acquired within the retry budget."""


class RedisLockManager:
    def __init__(self, redis: Redis, ttl_ms: int = 10_000, retries: int = 3) -> None:
        self._redis = redis
        self._ttl_ms = ttl_ms
        self._retries = retries

    @staticmethod
    def _key(file_path: str) -> str:
        digest = hashlib.sha256(file_path.encode()).hexdigest()
        return f"obsidian:lock:{digest}"

    async def acquire(self, file_path: str) -> str:
        """Acquire lock; return lock token on success or raise LockAcquisitionError."""
        key = self._key(file_path)
        token = secrets.token_hex(16)

        for attempt in range(self._retries):
            ok = await self._redis.set(key, token, px=self._ttl_ms, nx=True)
            if ok:
                log.info("lock.acquired", file_path=file_path, attempt=attempt)
                return token

            # Exponential backoff with jitter
            delay_ms = _BASE_BACKOFF_MS * (2 ** attempt) + random.randint(0, _JITTER_MS)
            log.debug("lock.retry", file_path=file_path, attempt=attempt, delay_ms=delay_ms)
            await asyncio.sleep(delay_ms / 1000)

        log.error("lock.failed", file_path=file_path, retries=self._retries)
        raise LockAcquisitionError(f"Could not acquire lock for {file_path!r} after {self._retries} attempts")

    async def release(self, file_path: str, token: str) -> None:
        """Release lock only if we still own it (token match via Lua script)."""
        key = self._key(file_path)
        # Atomic: compare-and-delete — prevents releasing another owner's lock
        lua = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        result = await self._redis.eval(lua, 1, key, token)
        if result:
            log.info("lock.released", file_path=file_path)
        else:
            log.warning("lock.release_missed", file_path=file_path, reason="token_mismatch_or_expired")
