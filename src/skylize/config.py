"""
Process configuration (pydantic-settings). Driver-free — safe to import anywhere.

`backend="memory"` wires in-memory fakes (tests / quick local run with no infra);
`backend="postgres"` wires the asyncpg + Redis concretes. The composition root
(bootstrap.py) reads this to build the service container.
"""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SKYLIZE_",
        extra="ignore",
        # Load a local .env (gitignored) so secrets like SKYLIZE_ANTHROPIC_API_KEY
        # can be set without exporting them into the shell. Real process env vars
        # still win over .env values.
        env_file=".env",
        env_file_encoding="utf-8",
    )

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

    # CORS allow-list for browser origins. Empty (default) = middleware not
    # installed: the gateway is a pure BFF today, so no cross-origin browser
    # calls exist. Never set "*" here — the gateway sends credentialed
    # responses, and a wildcard origin with credentials is a token leak.
    # From env, set a JSON array:
    # `SKYLIZE_CORS_ORIGINS='["https://console.skylize.com"]'`.
    cors_origins: list[str] = []

    # Human user auth (HS256 JWT access/refresh pair). Empty by default like the
    # other SKYLIZE_* secrets (knowledge_webhook_secret, n8n_api_key); the boot
    # check below fails closed when dev_auth is off and no secret is set, so
    # production never signs tokens with a missing/placeholder key.
    jwt_secret: str = ""
    jwt_access_token_ttl_minutes: int = 30
    jwt_refresh_token_ttl_days: int = 14

    # Credential vault at-rest encryption (Fernet key: urlsafe base64, 32 bytes).
    # Empty → the composition root mints an ephemeral dev key (memory backend);
    # in production set this so stored credentials survive a restart.
    credential_encryption_key: str = ""

    # Rate limiting (per org, per window).
    rate_limit_per_minute: int = 120
    # Tighter dedicated budget for the sensitive GET /credentials/resolve path.
    credential_resolve_rate_per_minute: int = 10

    # Event bus tuning
    dlq_after_retries: int = 5

    # Which Decision Engine the composition root wires (SKYLIZE_DECISION_ENGINE).
    #   "inline" (default) — app/decision_engine, the canonical port-based engine
    #                        and the sole emitter of terminal decision.* events.
    #   "opa"              — the OPA-backed decision_engine package. NOT yet
    #                        wired: its consumer transport must first be rebuilt
    #                        onto the EventBus port (decision_engine/constants.py).
    #                        bootstrap fails closed on "opa" rather than silently
    #                        running inline, so no environment flips it by accident.
    decision_engine: Literal["inline", "opa"] = "inline"

    # Decision Engine: org_ids to auto-subscribe its consumers to at startup.
    # Empty (default) leaves the engine wired but idle; tenants are subscribed
    # as they are provisioned. From env, set a JSON array:
    # `SKYLIZE_DECISION_ENGINE_ORG_IDS='["org_a","org_b"]'`.
    decision_engine_org_ids: list[str] = []

    # Temporal worker (app/orchestrator/temporal/worker.py). Address is the
    # host:port of the Temporal frontend — local dev server by default;
    # Temporal Cloud sets all three plus TLS via its own DSN conventions later.
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "skylize-workflows"

    # n8n → Skylize bridge key (X-Skylize-API-Key header on agent-prompts endpoint)
    n8n_api_key: str = ""

    # Web-search tool provider (tools/builtin/web_search.py). Empty api key =
    # NullWebSearchPort (tool returns an honest empty result set).
    search_provider: str = "brave"
    search_api_key: str = ""

    # HMAC-SHA256 secrets for inbound webhook verification; empty = check disabled.
    knowledge_webhook_secret: str = ""    # X-Hub-Signature-256 from n8n knowledge ingest
    agent_prompts_hmac_secret: str = ""  # X-Skylize-Signature on agent-prompts endpoint

    # LLM provider keys
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # Memory backends
    mem0_api_key: str = ""  # Mem0 cloud API key; empty = Mem0 adapter disabled

    # Knowledge ingestion pipeline (Qdrant + OpenAI embeddings)
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""

    # Logical model name → Anthropic model ID
    llm_model_default: str = "claude-sonnet-4-6"
    llm_model_fast: str = "claude-haiku-4-5-20251001"
    llm_model_reasoning: str = "claude-opus-4-6"

    # Pricing per 1M tokens in USD (configurable so ops can update without redeploy)
    llm_price_sonnet_in: float = 3.0
    llm_price_sonnet_out: float = 15.0
    llm_price_haiku_in: float = 0.80
    llm_price_haiku_out: float = 4.0
    llm_price_opus_in: float = 15.0
    llm_price_opus_out: float = 75.0

    @model_validator(mode="after")
    def _forbid_wildcard_cors(self) -> "Settings":
        # The gateway registers CORSMiddleware with allow_credentials=True; a
        # wildcard origin in that mode would hand credentialed responses to any
        # site. Fail at boot instead of shipping the leak.
        if "*" in self.cors_origins:
            raise ValueError(
                "SKYLIZE_CORS_ORIGINS must enumerate origins; '*' is not allowed"
            )
        return self

    @model_validator(mode="after")
    def _require_jwt_secret_when_prod(self) -> "Settings":
        # Fail closed at boot: with dev_auth off there is no header-trust path, so
        # a missing JWT signing key means user tokens cannot be issued/verified.
        if not self.dev_auth and not self.jwt_secret:
            raise ValueError(
                "SKYLIZE_JWT_SECRET must be set when dev_auth is disabled"
            )
        return self


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
