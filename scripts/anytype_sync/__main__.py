from __future__ import annotations

import asyncio
import sys

import structlog

from .config import Settings
from .sync import run_sync

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ]
)

log = structlog.get_logger(__name__)


def main() -> None:
    settings = Settings()
    try:
        asyncio.run(run_sync(settings))
    except Exception as exc:
        log.error("sync.fatal", error=str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
