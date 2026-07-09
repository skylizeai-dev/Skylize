from __future__ import annotations

import pathlib

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    anytype_api_key: str = ""
    anytype_base_url: str = "http://localhost:31009"
    anytype_space_id: str
    skylize_api_base_url: str
    skylize_webhook_secret: str = ""
    sync_state_path: str = str(
        pathlib.Path(__file__).parent / ".state" / "sync_state.json"
    )

    # Multi-tenant credential vault integration.
    # When both are set, anytype_api_key is fetched from the Skylize vault at
    # startup instead of read from env. anytype_api_key may then be left empty.
    skylize_auth_token: str = ""
    org_id: str = ""
    resolve_credential_url: str = ""  # defaults to {skylize_api_base_url}/api/v1/credentials/resolve
