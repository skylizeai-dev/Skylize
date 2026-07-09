"""ObsidianWriter — lock-guarded atomic writes to Obsidian vault .md files."""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import aiofiles
import aiofiles.os
import structlog

from .lock import RedisLockManager

log = structlog.get_logger(__name__)


def _normalize(p: Path) -> str:
    r"""Canonical, case-normalized absolute path string — strips Windows \\?\ prefix."""
    s = os.path.normcase(os.path.abspath(p))
    # Strip Windows extended-path prefix if present
    if s.startswith("\\\\?\\"):
        s = s[4:]
    return s


class PathTraversalError(ValueError):
    """Raised when file_path escapes the allowed vault root."""


@dataclass(frozen=True)
class WriteResult:
    file_path: str
    bytes_written: int
    org_id: str


class ObsidianWriter:
    def __init__(self, vault_root: str, lock_manager: RedisLockManager) -> None:
        self._vault_root = Path(vault_root).resolve()
        self._vault_root_str = _normalize(self._vault_root)
        self._lock = lock_manager

    def _safe_path(self, file_path: str) -> Path:
        """Resolve and verify path stays within vault root.

        URL-decoded first so that %2e%2e traversal is caught before resolve().
        """
        decoded = urllib.parse.unquote(file_path)
        resolved = (self._vault_root / decoded).resolve()
        resolved_str = _normalize(resolved)
        sentinel = self._vault_root_str + os.sep
        if resolved_str != self._vault_root_str and not resolved_str.startswith(sentinel):
            raise PathTraversalError(f"Path escapes vault root: {file_path!r}")
        return resolved

    async def write(self, file_path: str, content: str, org_id: str) -> WriteResult:
        target = self._safe_path(file_path)
        log.info("write.start", file_path=file_path, org_id=org_id)

        token = await self._lock.acquire(file_path)
        try:
            result = await self._atomic_write(target, content, file_path, org_id)
        finally:
            await self._lock.release(file_path, token)

        log.info("write.done", file_path=file_path, org_id=org_id, bytes=result.bytes_written)
        return result

    async def append(self, file_path: str, content: str, org_id: str) -> WriteResult:
        target = self._safe_path(file_path)
        log.info("append.start", file_path=file_path, org_id=org_id)

        token = await self._lock.acquire(file_path)
        try:
            existing = ""
            if target.exists():
                async with aiofiles.open(target, "r", encoding="utf-8") as fh:
                    existing = await fh.read()
            combined = existing + content
            result = await self._atomic_write(target, combined, file_path, org_id)
        finally:
            await self._lock.release(file_path, token)

        log.info("append.done", file_path=file_path, org_id=org_id, bytes=result.bytes_written)
        return result

    async def _atomic_write(self, target: Path, content: str, file_path: str, org_id: str) -> WriteResult:
        """Write to .tmp, fsync, rename — atomic on POSIX; best-effort on Windows."""
        await aiofiles.os.makedirs(target.parent, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")

        encoded = content.encode("utf-8")
        try:
            async with aiofiles.open(tmp, "wb") as fh:
                await fh.write(encoded)
                await fh.flush()
                os.fsync(fh.fileno())

            # os.replace is atomic on POSIX; on Windows it replaces atomically if on same drive
            await aiofiles.os.replace(tmp, target)
        except Exception:
            # Best-effort cleanup of tmp file on failure
            try:
                await aiofiles.os.remove(tmp)
            except OSError:
                pass
            log.error("write.failed", file_path=file_path, org_id=org_id)
            raise

        return WriteResult(file_path=file_path, bytes_written=len(encoded), org_id=org_id)
