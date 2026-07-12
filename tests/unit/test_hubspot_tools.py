"""Unit tests for integration.hubspot_create_contact / hubspot_search_contacts.

HTTP is mocked at `httpx.AsyncClient` — no network calls. `CredentialVault` is
a real instance backed by an in-memory fake repo so credential resolution
(including cross-org isolation) is exercised for real, not mocked away.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest

from skylize.app.credentials.encryption import FernetEncryptor
from skylize.app.credentials.vault import CredentialVault
from skylize.dal.credentials import InMemoryCredentialRepository
from skylize.tools.base import ToolContext, ToolError
from skylize.tools.builtin.hubspot_tools import (
    build_hubspot_create_contact_tool,
    build_hubspot_search_contacts_tool,
)


def _response(status_code: int, json_body: dict) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=json_body,
        request=httpx.Request("POST", "https://api.hubapi.com/crm/v3/objects/contacts"),
    )


class _FakeAudit:
    async def record(self, **kwargs) -> None:
        return None


def _build_vault() -> tuple[CredentialVault, FernetEncryptor, InMemoryCredentialRepository]:
    encryptor = FernetEncryptor(FernetEncryptor.generate_key())
    repo = InMemoryCredentialRepository()
    vault = CredentialVault(encryptor, repo, _FakeAudit())
    return vault, encryptor, repo


async def _seed(repo: InMemoryCredentialRepository, encryptor: FernetEncryptor, org_id: str, token: str) -> None:
    from datetime import datetime, timezone
    from uuid import uuid4

    from skylize.dal.credentials import CredentialRow

    await repo.insert(
        CredentialRow(
            cred_id=uuid4(),
            org_id=org_id,
            provider="hubspot",
            label="",
            encrypted_value=encryptor.encrypt(token),
            metadata_json="{}",
            created_at=datetime.now(timezone.utc),
            rotated_at=None,
        )
    )


def _ctx(org_id: str) -> ToolContext:
    return ToolContext(org_id=org_id, agent_id="crm_agent", correlation_id=uuid4())


async def test_create_contact_credential_not_configured_raises_tool_error() -> None:
    vault, _, _ = _build_vault()
    tool = build_hubspot_create_contact_tool(vault)
    inp = tool.input_schema.model_validate({"email": "a@example.com"})

    with pytest.raises(ToolError):
        await tool.handler(inp, _ctx("org_no_hubspot"))


async def test_search_contacts_credential_not_configured_raises_tool_error() -> None:
    vault, _, _ = _build_vault()
    tool = build_hubspot_search_contacts_tool(vault)
    inp = tool.input_schema.model_validate({"query": "acme"})

    with pytest.raises(ToolError):
        await tool.handler(inp, _ctx("org_no_hubspot"))


async def test_create_contact_success_sends_correct_payload_and_bearer_token() -> None:
    vault, encryptor, repo = _build_vault()
    await _seed(repo, encryptor, "org_a", "secret-token-a")
    tool = build_hubspot_create_contact_tool(vault)

    mock_client = AsyncMock()
    mock_client.post.return_value = _response(201, {"id": "123", "properties": {}})
    mock_client.aclose = AsyncMock()

    with patch(
        "skylize.tools.builtin.hubspot_tools.httpx.AsyncClient", return_value=mock_client
    ) as mock_ctor:
        inp = tool.input_schema.model_validate(
            {"email": "a@example.com", "first_name": "Ada", "company": "Acme"}
        )
        out = await tool.handler(inp, _ctx("org_a"))

    assert out.contact_id == "123"
    assert out.created is True
    mock_ctor.assert_called_once()
    assert mock_ctor.call_args.kwargs["headers"]["Authorization"] == "Bearer secret-token-a"
    mock_client.post.assert_awaited_once_with(
        "/crm/v3/objects/contacts",
        json={"properties": {"email": "a@example.com", "firstname": "Ada", "company": "Acme"}},
    )


async def test_create_contact_duplicate_resolves_via_search() -> None:
    vault, encryptor, repo = _build_vault()
    await _seed(repo, encryptor, "org_a", "secret-token-a")
    tool = build_hubspot_create_contact_tool(vault)

    mock_client = AsyncMock()
    mock_client.post.side_effect = [
        _response(409, {"message": "Contact already exists"}),
        _response(200, {"results": [{"id": "999", "properties": {"email": "a@example.com"}}]}),
    ]
    mock_client.aclose = AsyncMock()

    with patch("skylize.tools.builtin.hubspot_tools.httpx.AsyncClient", return_value=mock_client):
        inp = tool.input_schema.model_validate({"email": "a@example.com"})
        out = await tool.handler(inp, _ctx("org_a"))

    assert out.contact_id == "999"
    assert out.created is False


async def test_create_contact_api_error_propagates_as_tool_error() -> None:
    vault, encryptor, repo = _build_vault()
    await _seed(repo, encryptor, "org_a", "secret-token-a")
    tool = build_hubspot_create_contact_tool(vault)

    mock_client = AsyncMock()
    mock_client.post.return_value = _response(401, {"message": "invalid token"})
    mock_client.aclose = AsyncMock()

    with patch("skylize.tools.builtin.hubspot_tools.httpx.AsyncClient", return_value=mock_client):
        inp = tool.input_schema.model_validate({"email": "a@example.com"})
        with pytest.raises(ToolError):
            await tool.handler(inp, _ctx("org_a"))


async def test_create_contact_retries_on_429_then_succeeds() -> None:
    vault, encryptor, repo = _build_vault()
    await _seed(repo, encryptor, "org_a", "secret-token-a")
    tool = build_hubspot_create_contact_tool(vault)

    mock_client = AsyncMock()
    mock_client.post.side_effect = [
        _response(429, {"message": "rate limited"}),
        _response(429, {"message": "rate limited"}),
        _response(201, {"id": "123", "properties": {}}),
    ]
    mock_client.aclose = AsyncMock()

    with patch("skylize.tools.builtin.hubspot_tools.httpx.AsyncClient", return_value=mock_client):
        inp = tool.input_schema.model_validate({"email": "a@example.com"})
        out = await tool.handler(inp, _ctx("org_a"))

    assert out.contact_id == "123"
    assert mock_client.post.await_count == 3


async def test_create_contact_exhausts_retries_on_persistent_429() -> None:
    vault, encryptor, repo = _build_vault()
    await _seed(repo, encryptor, "org_a", "secret-token-a")
    tool = build_hubspot_create_contact_tool(vault)

    mock_client = AsyncMock()
    mock_client.post.return_value = _response(429, {"message": "rate limited"})
    mock_client.aclose = AsyncMock()

    with patch("skylize.tools.builtin.hubspot_tools.httpx.AsyncClient", return_value=mock_client):
        inp = tool.input_schema.model_validate({"email": "a@example.com"})
        with pytest.raises(ToolError):
            await tool.handler(inp, _ctx("org_a"))

    assert mock_client.post.await_count == 3


async def test_search_contacts_returns_typed_list() -> None:
    vault, encryptor, repo = _build_vault()
    await _seed(repo, encryptor, "org_a", "secret-token-a")
    tool = build_hubspot_search_contacts_tool(vault)

    mock_client = AsyncMock()
    mock_client.post.return_value = _response(
        200,
        {
            "results": [
                {
                    "id": "1",
                    "properties": {"email": "a@x.com", "firstname": "Ada", "lastname": "Lovelace"},
                },
            ]
        },
    )
    mock_client.aclose = AsyncMock()

    with patch("skylize.tools.builtin.hubspot_tools.httpx.AsyncClient", return_value=mock_client):
        inp = tool.input_schema.model_validate({"query": "ada", "limit": 5})
        out = await tool.handler(inp, _ctx("org_a"))

    assert len(out.contacts) == 1
    assert out.contacts[0].id == "1"
    assert out.contacts[0].email == "a@x.com"
    assert out.contacts[0].first_name == "Ada"
    assert out.contacts[0].last_name == "Lovelace"
    mock_client.post.assert_awaited_once_with(
        "/crm/v3/objects/contacts/search",
        json={"query": "ada", "limit": 5, "properties": ["email", "firstname", "lastname"]},
    )


async def test_cross_org_isolation_uses_only_the_calling_orgs_credential() -> None:
    vault, encryptor, repo = _build_vault()
    await _seed(repo, encryptor, "org_a", "token-a")
    await _seed(repo, encryptor, "org_b", "token-b")
    tool = build_hubspot_create_contact_tool(vault)

    seen_tokens: list[str] = []

    def _make_client(**kwargs):
        seen_tokens.append(kwargs["headers"]["Authorization"])
        client = AsyncMock()
        client.post.return_value = _response(201, {"id": "1", "properties": {}})
        client.aclose = AsyncMock()
        return client

    with patch("skylize.tools.builtin.hubspot_tools.httpx.AsyncClient", side_effect=_make_client):
        inp = tool.input_schema.model_validate({"email": "a@example.com"})
        await tool.handler(inp, _ctx("org_a"))
        await tool.handler(inp, _ctx("org_b"))

    assert seen_tokens == ["Bearer token-a", "Bearer token-b"]
