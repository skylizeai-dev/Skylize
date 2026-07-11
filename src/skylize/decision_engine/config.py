from __future__ import annotations

import socket

from pydantic_settings import BaseSettings, SettingsConfigDict


class DecisionEngineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SKYLIZE_", extra="ignore")

    opa_url: str = "http://localhost:8181"
    opa_policy_path: str = "skylize/decision/allow"
    opa_timeout_seconds: float = 2.0

    redis_url: str = "redis://localhost:6379"
    redis_consumer_group: str = "cg:decision_engine"
    redis_consumer_name: str = socket.gethostname()
    redis_dlq_stream: str = "evt:dlq:decision_engine"
    redis_idle_time_ms: int = 60000
    redis_max_retries: int = 3
    redis_batch_size: int = 10

    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_host: str = "https://cloud.langfuse.com"

    database_url: str

    capital_reserve_floor_pct: float = 0.15
    hitl_expiry_hours: int = 48
