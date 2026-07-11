"""Tests for anytype_sync.sync.resolve_anytype_api_key — vault credential fetch."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from anytype_sync.config import Settings
from anytype_sync.sync import resolve_anytype_api_key


def _settings(**kwargs: str) -> Settings:
    base = dict(
        anytype_space_id="space-1",
        skylize_api_base_url="https://skylize.example.com",
    )
    base.update(kwargs)
    return Settings.model_validate(base)


# ---------------------------------------------------------------------------
# Env-var fallback (no vault configured)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fallback_to_env_var_when_no_auth_token() -> None:
    settings = _settings(anytype_api_key="env-key-123")
    key = await resolve_anytype_api_key(settings)
    assert key == "env-key-123"


@pytest.mark.asyncio
async def test_fallback_to_env_var_when_no_org_id() -> None:
    settings = _settings(anytype_api_key="env-key-456", skylize_auth_token="tok")
    key = await resolve_anytype_api_key(settings)
    assert key == "env-key-456"


@pytest.mark.asyncio
async def test_raises_when_no_key_and_vault_not_configured() -> None:
    settings = _settings()  # no anytype_api_key, no auth_token/org_id
    with pytest.raises(RuntimeError, match="anytype_api_key must be set"):
        await resolve_anytype_api_key(settings)


# ---------------------------------------------------------------------------
# Vault fetch (auth_token + org_id both set)
# ---------------------------------------------------------------------------

def _mock_response(value: str, status: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = {"provider": "anytype", "value": value}
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.asyncio
async def test_fetches_key_from_vault() -> None:
    settings = _settings(skylize_auth_token="Bearer tok", org_id="org_a")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_response("vault-key-xyz"))

    with patch("anytype_sync.sync.httpx.AsyncClient", return_value=mock_client):
        key = await resolve_anytype_api_key(settings)

    assert key == "vault-key-xyz"
    mock_client.get.assert_awaited_once()
    call_kwargs = mock_client.get.call_args
    assert call_kwargs.kwargs["params"] == {"provider": "anytype"}
    assert call_kwargs.kwargs["headers"]["Authorization"] == "ApiKey Bearer tok"


@pytest.mark.asyncio
async def test_uses_default_resolve_url() -> None:
    settings = _settings(skylize_auth_token="tok", org_id="org_a")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_response("key"))

    with patch("anytype_sync.sync.httpx.AsyncClient", return_value=mock_client):
        await resolve_anytype_api_key(settings)

    url = mock_client.get.call_args.args[0]
    assert url == "https://skylize.example.com/api/v1/credentials/resolve"


@pytest.mark.asyncio
async def test_uses_custom_resolve_url() -> None:
    settings = _settings(
        skylize_auth_token="tok",
        org_id="org_a",
        resolve_credential_url="https://custom.host/creds/resolve",
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_response("key"))

    with patch("anytype_sync.sync.httpx.AsyncClient", return_value=mock_client):
        await resolve_anytype_api_key(settings)

    url = mock_client.get.call_args.args[0]
    assert url == "https://custom.host/creds/resolve"


@pytest.mark.asyncio
async def test_vault_http_error_propagates() -> None:
    settings = _settings(skylize_auth_token="tok", org_id="org_a")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_response("", status=401))

    with patch("anytype_sync.sync.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(httpx.HTTPStatusError):
            await resolve_anytype_api_key(settings)


@pytest.mark.asyncio
async def test_vault_takes_precedence_over_env_var() -> None:
    """When vault is configured, it wins even if anytype_api_key is also set."""
    settings = _settings(
        anytype_api_key="env-key",
        skylize_auth_token="tok",
        org_id="org_a",
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_response("vault-key"))

    with patch("anytype_sync.sync.httpx.AsyncClient", return_value=mock_client):
        key = await resolve_anytype_api_key(settings)

    assert key == "vault-key"
