"""
Process configuration (pydantic-settings). Driver-free — safe to import anywhere.

`backend="memory"` wires in-memory fakes (tests / quick local run with no infra);
`backend="postgres"` wires the asyncpg + Redis concretes. The composition root
(bootstrap.py) reads this to build the service container.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SKYLIZE_", extra="ignore")

    backend: Literal["memory", "postgres"] = "memory"

    # Two DSNs by privilege (Sprint-2 RLS fix):
    #   db_url      — admin/superuser; used ONLY for migrations + extension setup.
    #   db_app_url  — the non-superuser `skylize_app` role the RUNTIME connects as.
    #                 It is NOBYPASSRLS, so tenant isolation actually applies. If
    #                 unset, falls back to db_url (acceptable only for local/dev;
    #                 production MUST set a distinct non-superuser app DSN).
    db_url: str = "postgresql://skylize:localdev@localhost:5432/skylize"
    db_app_url: str = ""
    redis_url: str = "redis://localhost:6379"

    @property
    def runtime_db_url(self) -> str:
        """The DSN the application runtime uses (app role if set, else db_url)."""
        return self.db_app_url or self.db_url

    # Governance signing key (PKCS8 PEM, ECDSA P-384). REQUIRED in production:
    # when backend != "memory" and this is empty, startup fails closed (an
    # ephemeral per-pod key would break multi-instance token verification — see
    # app/governance/keys.py). Only the in-memory/dev backend may run without it.
    # Inject from the secrets manager; generate with scripts/gen_governance_key.py.
    governance_signing_key_pem: str = ""

    # Token validity window (minutes) and run defaults.
    token_ttl_minutes: int = 5

    # Edge auth: dev mode trusts X-Dev-* headers; production verifies OIDC JWTs.
    dev_auth: bool = True
    oidc_jwks_url: str = ""
    oidc_audience: str = ""
    request_context_ttl_seconds: int = 300

    # Rate limiting (per org, per window).
    rate_limit_per_minute: int = 120

    # Event bus tuning
    dlq_after_retries: int = 5


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
