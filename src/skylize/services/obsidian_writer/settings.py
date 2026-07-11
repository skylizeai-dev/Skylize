"""obsidian_writer service configuration — all values from environment."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ObsidianSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OBSIDIAN_", extra="ignore")

    vault_root: str  # absolute path; no default — must be set
    redis_url: str = "redis://localhost:6379"
    lock_ttl_ms: int = 10_000
    hmac_secret: str  # no default — must be set
    rate_limit_per_minute: int = 100
    service_port: int = 8001


@lru_cache(maxsize=1)
def get_settings() -> ObsidianSettings:
    return ObsidianSettings()
