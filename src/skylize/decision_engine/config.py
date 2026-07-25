from __future__ import annotations

import socket

from pydantic_settings import BaseSettings, SettingsConfigDict


class DecisionEngineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SKYLIZE_", extra="ignore")

    opa_url: str = "http://localhost:8181"
    # Package path (guardrails.md §5), NOT a leaf rule — OPA's /v1/data/<package>
    # returns the whole package document {allow, require_human, deny,
    # policy_version} as an object, matching OPAClient's object-based parsing.
    opa_policy_path: str = "skylize/decision"
    opa_timeout_seconds: float = 2.0

    redis_url: str = "redis://localhost:6379"
    redis_consumer_group: str = "cg:decision_engine"
    redis_consumer_name: str = socket.gethostname()
    redis_idle_time_ms: int = 60000
    redis_max_retries: int = 3

    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_host: str = "https://cloud.langfuse.com"

    database_url: str

    capital_reserve_floor_pct: float = 0.15
    hitl_expiry_hours: int = 48
