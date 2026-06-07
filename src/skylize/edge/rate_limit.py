"""
Per-tenant rate limiting at the edge (system_boundaries.md §5.1).

A minimal fixed-window counter per `org_id`. MVP-grade and in-process; at Scale
this moves behind Redis (per-org INCR with EXPIRE) without changing callers.
"""

from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self._limit = per_minute
        self._window: dict[str, tuple[int, int]] = {}  # org -> (window_start, count)

    def allow(self, org_id: str) -> bool:
        now = int(time.time())
        window_start = now - (now % 60)
        start, count = self._window.get(org_id, (window_start, 0))
        if start != window_start:
            start, count = window_start, 0
        if count >= self._limit:
            self._window[org_id] = (start, count)
            return False
        self._window[org_id] = (start, count + 1)
        return True
