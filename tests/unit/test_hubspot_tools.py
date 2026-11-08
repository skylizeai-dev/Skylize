"""Unit tests for integration.hubspot_create_contact / hubspot_search_contacts.

HTTP is mocked at `httpx.AsyncClient` — no network calls. `CredentialVault` is
a real instance backed by an in-memory fake repo so credential resolution
(including cross-org isolation) is exercised for real, not mocked away.

OD4 (owner ruling): `create_contact` is a non-idempotent POST, so it must never
retry a 5xx or a post-send timeout (the contact may already exist). It may retry
only HTTP 429 (honoring a bounded Retry-After) and pre-send connect failures;
every ambiguous outcome is surfaced as ToolExecutionError, never swallowed.
Retry sleeps are patched out (see `_patched_sleep`) so no test really waits.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest

from skylize.app.credentials.encryption import FernetEncryptor
from skylize.app.credentials.vault import CredentialVault
from skylize.dal.credentials import InMemoryCredentialRepository
from skylize.tools.base import ToolContext, ToolError, ToolExecutionError
from skylize.tools.builtin.hubspot_tools import (
    HubSpotClient,
    build_hubspot_create_contact_tool,
    build_hubspot_search_contacts_tool,
)


def _response(
    status_code: int, json_body: dict, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=json_body,
        headers=headers,
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


@contextmanager
def _patched_sleep(method) -> Iterator[AsyncMock]:
    """Replace the tenacity sleep of a @retry-wrapped method; yields the mock.

    tenacity's async wrapper copies `method.retry` per call and awaits
    `copy.sleep(seconds)`, so patching `.retry.sleep` captures every wait.
    """
    sleep = AsyncMock()
    with patch.object(method.retry, "sleep", sleep):
        yield sleep


def _waited(sleep: AsyncMock) -> list[float]:
    return [float(c.args[0]) for c in sleep.await_args_list]


async def _create_tool_and_ctx():
    vault, encryptor, repo = _build_vault()
    await _seed(repo, encryptor, "org_a", "secret-token-a")
    tool = build_hubspot_create_contact_tool(vault)
    inp = tool.input_schema.model_validate({"email": "a@example.com"})
    return tool, inp, _ctx("org_a")


def _mock_client(post_effect) -> AsyncMock:
    """post_effect: a list / exception (side_effect) or a single response."""
    client = AsyncMock()
    if isinstance(post_effect, (list, BaseException)):
        client.post.side_effect = post_effect
    else:
        client.post.return_value = post_effect
    client.aclose = AsyncMock()
    return client


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

    with (
        patch("skylize.tools.builtin.hubspot_tools.httpx.AsyncClient", return_value=mock_client),
        _patched_sleep(HubSpotClient.create_contact),
    ):
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

    with (
        patch("skylize.tools.builtin.hubspot_tools.httpx.AsyncClient", return_value=mock_client),
        _patched_sleep(HubSpotClient.create_contact),
    ):
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


# --- OD4: non-idempotent create_contact retry policy -----------------------

_AMBIGUOUS = ("OUTCOME UNKNOWN", "Do NOT re-run")
_PATCH_CLIENT = "skylize.tools.builtin.hubspot_tools.httpx.AsyncClient"


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_create_contact_5xx_sends_exactly_one_request_and_surfaces_outcome_unknown(
    status: int,
) -> None:
    tool, inp, ctx = await _create_tool_and_ctx()
    client = _mock_client(_response(status, {"message": "server error"}))

    with (
        patch(_PATCH_CLIENT, return_value=client),
        _patched_sleep(HubSpotClient.create_contact) as sleep,
    ):
        with pytest.raises(ToolExecutionError) as exc_info:
            await tool.handler(inp, ctx)

    assert client.post.await_count == 1
    msg = str(exc_info.value)
    for marker in _AMBIGUOUS:
        assert marker in msg
    assert str(status) in msg
    sleep.assert_not_awaited()
    client.aclose.assert_awaited()


async def test_create_contact_read_timeout_sends_exactly_one_request() -> None:
    tool, inp, ctx = await _create_tool_and_ctx()
    client = _mock_client(httpx.ReadTimeout("timed out"))

    with (
        patch(_PATCH_CLIENT, return_value=client),
        _patched_sleep(HubSpotClient.create_contact) as sleep,
    ):
        with pytest.raises(ToolExecutionError) as exc_info:
            await tool.handler(inp, ctx)

    assert client.post.await_count == 1
    for marker in _AMBIGUOUS:
        assert marker in str(exc_info.value)
    sleep.assert_not_awaited()


@pytest.mark.parametrize(
    "exc",
    [
        httpx.WriteTimeout("write timed out"),
        httpx.ReadError("read failed"),
        httpx.RemoteProtocolError("server disconnected"),
    ],
    ids=["WriteTimeout", "ReadError", "RemoteProtocolError"],
)
async def test_create_contact_post_send_transport_errors_are_not_retried(
    exc: httpx.TransportError,
) -> None:
    tool, inp, ctx = await _create_tool_and_ctx()
    client = _mock_client(exc)

    with (
        patch(_PATCH_CLIENT, return_value=client),
        _patched_sleep(HubSpotClient.create_contact) as sleep,
    ):
        with pytest.raises(ToolExecutionError) as exc_info:
            await tool.handler(inp, ctx)

    assert client.post.await_count == 1
    for marker in _AMBIGUOUS:
        assert marker in str(exc_info.value)
    sleep.assert_not_awaited()


async def test_create_contact_429_retried_honoring_retry_after() -> None:
    tool, inp, ctx = await _create_tool_and_ctx()
    client = _mock_client(
        [
            _response(429, {"message": "rate limited"}, headers={"Retry-After": "2"}),
            _response(201, {"id": "123", "properties": {}}),
        ]
    )

    with (
        patch(_PATCH_CLIENT, return_value=client),
        _patched_sleep(HubSpotClient.create_contact) as sleep,
    ):
        out = await tool.handler(inp, ctx)

    assert out.contact_id == "123"
    assert client.post.await_count == 2
    assert _waited(sleep) == [2.0]


async def test_create_contact_429_retry_after_is_bounded() -> None:
    tool, inp, ctx = await _create_tool_and_ctx()
    client = _mock_client(
        [
            _response(429, {"message": "rate limited"}, headers={"Retry-After": "3600"}),
            _response(201, {"id": "123", "properties": {}}),
        ]
    )

    with (
        patch(_PATCH_CLIENT, return_value=client),
        _patched_sleep(HubSpotClient.create_contact) as sleep,
    ):
        out = await tool.handler(inp, ctx)

    assert out.contact_id == "123"
    assert _waited(sleep) == [10.0]


async def test_create_contact_persistent_429_stops_after_three_attempts() -> None:
    tool, inp, ctx = await _create_tool_and_ctx()
    client = _mock_client(_response(429, {"message": "rate limited"}))

    with (
        patch(_PATCH_CLIENT, return_value=client),
        _patched_sleep(HubSpotClient.create_contact),
    ):
        with pytest.raises(ToolError):
            await tool.handler(inp, ctx)

    assert client.post.await_count == 3


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("refused"),
        httpx.ConnectTimeout("connect timed out"),
        httpx.PoolTimeout("pool exhausted"),
    ],
    ids=["ConnectError", "ConnectTimeout", "PoolTimeout"],
)
async def test_create_contact_connect_failure_is_retried(exc: httpx.TransportError) -> None:
    tool, inp, ctx = await _create_tool_and_ctx()
    client = _mock_client([exc, _response(201, {"id": "123", "properties": {}})])

    with (
        patch(_PATCH_CLIENT, return_value=client),
        _patched_sleep(HubSpotClient.create_contact) as sleep,
    ):
        out = await tool.handler(inp, ctx)

    assert out.contact_id == "123"
    assert client.post.await_count == 2
    assert sleep.await_count == 1


async def test_create_contact_connect_failure_exhausted_reports_not_sent() -> None:
    tool, inp, ctx = await _create_tool_and_ctx()
    client = _mock_client(httpx.ConnectError("refused"))

    with (
        patch(_PATCH_CLIENT, return_value=client),
        _patched_sleep(HubSpotClient.create_contact),
    ):
        with pytest.raises(ToolExecutionError) as exc_info:
            await tool.handler(inp, ctx)

    assert client.post.await_count == 3
    assert "was NOT sent" in str(exc_info.value)


async def test_search_contacts_still_retries_5xx_read_only() -> None:
    vault, encryptor, repo = _build_vault()
    await _seed(repo, encryptor, "org_a", "secret-token-a")
    tool = build_hubspot_search_contacts_tool(vault)
    client = _mock_client(
        [
            _response(500, {"message": "server error"}),
            _response(200, {"results": [{"id": "1", "properties": {"email": "a@x.com"}}]}),
        ]
    )

    with (
        patch(_PATCH_CLIENT, return_value=client),
        _patched_sleep(HubSpotClient.search_contacts),
    ):
        inp = tool.input_schema.model_validate({"query": "ada"})
        out = await tool.handler(inp, _ctx("org_a"))

    assert client.post.await_count == 2
    assert out.contacts[0].id == "1"
