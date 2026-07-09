"""
Unit tests for obsidian_writer service.

Run:
    pytest tests/unit/test_obsidian_writer.py -v
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from skylize.services.obsidian_writer.lock import LockAcquisitionError, RedisLockManager
from skylize.services.obsidian_writer.writer import ObsidianWriter, PathTraversalError


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


class FakeRedis:
    """Minimal in-memory Redis fake supporting SET NX PX, GET, DEL, eval."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, px: int | None = None, nx: bool = False) -> bool | None:
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def delete(self, *keys: str) -> int:
        removed = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                removed += 1
        return removed

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        # Implements the compare-and-delete Lua script used in release()
        key, token = args[0], args[1]
        if self._store.get(key) == token:
            del self._store[key]
            return 1
        return 0

    async def incr(self, key: str) -> int:
        val = int(self._store.get(key, "0")) + 1
        self._store[key] = str(val)
        return val

    async def expire(self, key: str, seconds: int) -> None:
        pass  # not needed for unit tests

    async def aclose(self) -> None:
        pass


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def lock_manager(fake_redis: FakeRedis) -> RedisLockManager:
    return RedisLockManager(fake_redis, ttl_ms=5_000, retries=3)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def writer(vault: Path, lock_manager: RedisLockManager) -> ObsidianWriter:
    return ObsidianWriter(vault_root=str(vault), lock_manager=lock_manager)


# ---------------------------------------------------------------------------
# test_concurrent_writes_are_serialized
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_writes_are_serialized(vault: Path) -> None:
    """10 concurrent tasks writing different content to the same file — final
    content must exactly equal one of the payloads (no interleaving)."""

    # Real Redis not available in CI; use a threading-safe asyncio.Lock-backed fake
    # that serialises SET NX properly across concurrent coroutines.
    class SerializedFakeRedis(FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self._mutex = asyncio.Lock()

        async def set(self, key: str, value: str, px: int | None = None, nx: bool = False) -> bool | None:
            async with self._mutex:
                return await super().set(key, value, px=px, nx=nx)

        async def eval(self, script: str, numkeys: int, *args: Any) -> int:
            async with self._mutex:
                return await super().eval(script, numkeys, *args)

    redis = SerializedFakeRedis()
    # retries=20 with short TTL ensures 10 competing tasks each eventually win their slot
    lock_mgr = RedisLockManager(redis, ttl_ms=5_000, retries=20)
    w = ObsidianWriter(vault_root=str(vault), lock_manager=lock_mgr)

    payloads = [f"content_{i}\n" for i in range(10)]
    target_rel = "notes/concurrent.md"

    results = await asyncio.gather(*[
        w.write(target_rel, payload, org_id="org_test")
        for payload in payloads
    ])

    # All writes must have succeeded
    assert len(results) == 10

    # Final file content must be exactly one of the payloads (no interleaving)
    final = (vault / target_rel).read_text(encoding="utf-8")
    assert final in payloads, f"Final content {final!r} is not a clean write — data interleaved"


# ---------------------------------------------------------------------------
# test_lock_releases_on_exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lock_releases_on_exception(vault: Path, fake_redis: FakeRedis) -> None:
    """Lock must be released even if the write operation raises."""
    lock_mgr = RedisLockManager(fake_redis, ttl_ms=5_000, retries=3)
    w = ObsidianWriter(vault_root=str(vault), lock_manager=lock_mgr)

    file_path = "notes/boom.md"
    lock_key = RedisLockManager._key(file_path)

    with patch(
        "skylize.services.obsidian_writer.writer.ObsidianWriter._atomic_write",
        new_callable=AsyncMock,
        side_effect=RuntimeError("disk exploded"),
    ):
        with pytest.raises(RuntimeError, match="disk exploded"):
            await w.write(file_path, "hello", org_id="org_test")

    # Lock must be gone from the fake store
    assert fake_redis._store.get(lock_key) is None, "Lock was not released after exception"


# ---------------------------------------------------------------------------
# test_path_traversal_rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("evil_path", [
    "../../../etc/passwd",
    "../../windows/system32/config/sam",
    "/etc/shadow",
    "notes/../../secret.md",          # goes up two levels, out of vault
    "notes/%2e%2e/%2e%2e/secret.md",  # URL-encoded double traversal, out of vault
])
async def test_path_traversal_rejected(writer: ObsidianWriter, evil_path: str) -> None:
    """Paths that escape vault root must raise PathTraversalError (-> HTTP 400)."""
    with pytest.raises(PathTraversalError):
        await writer.write(evil_path, "pwned", org_id="attacker")


# ---------------------------------------------------------------------------
# test_lock_acquisition_failure_raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lock_acquisition_failure_raises(vault: Path) -> None:
    """When Redis always returns None for SET NX, LockAcquisitionError is raised."""

    class AlwaysLockedRedis(FakeRedis):
        async def set(self, key: str, value: str, px: int | None = None, nx: bool = False) -> bool | None:
            return None  # always occupied

    redis = AlwaysLockedRedis()
    lock_mgr = RedisLockManager(redis, ttl_ms=100, retries=2)
    w = ObsidianWriter(vault_root=str(vault), lock_manager=lock_mgr)

    with pytest.raises(LockAcquisitionError):
        await w.write("notes/locked.md", "content", org_id="org_test")


# ---------------------------------------------------------------------------
# test_atomic_write_cleans_tmp_on_error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_atomic_write_tmp_removed_on_error(vault: Path, writer: ObsidianWriter) -> None:
    """If rename fails, the .tmp file must be cleaned up."""
    target = vault / "notes" / "fail.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    with patch("aiofiles.os.replace", new_callable=AsyncMock, side_effect=OSError("rename failed")):
        with pytest.raises(OSError):
            await writer._atomic_write(target, "data", "notes/fail.md", "org_test")

    tmp = target.with_suffix(target.suffix + ".tmp")
    assert not tmp.exists(), ".tmp file was not cleaned up after failed rename"
