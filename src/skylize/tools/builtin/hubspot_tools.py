"""integration.hubspot_create_contact / integration.hubspot_search_contacts.

First real per-org integration tools: credentials are resolved through
`CredentialVault.retrieve(org_id, "hubspot")` on every call — never cached
across calls, never read from an env var — so a token is always scoped to the
calling org and a rotated/disconnected credential takes effect immediately.

Mirrors `memory_recall.py`'s graceful-degradation shape: "HubSpot not
connected for this org" is an expected, common state (before a customer
connects their CRM), so it is raised as a `ToolExecutionError` with a clear
message — audited and returned to the caller cleanly by `ToolProxy.invoke` —
never an unhandled 500.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ...app.credentials.vault import CredentialNotFoundError, CredentialVault
from ..base import ToolContext, ToolDefinition, ToolExecutionError

_PROVIDER = "hubspot"
_BASE_URL = "https://api.hubapi.com"


class HubSpotCreateContactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None


class HubSpotCreateContactOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_id: str
    created: bool


class HubSpotSearchContactsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    limit: int = Field(default=10, gt=0, le=100)


class HubSpotContact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class HubSpotSearchContactsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contacts: list[HubSpotContact] = Field(default_factory=list)


_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return isinstance(exc, (httpx.ConnectError, httpx.TimeoutException))


class HubSpotClient:
    """Thin wrapper over HubSpot's REST API — no SDK dependency.

    One client per call, built from the token resolved for that call's
    `org_id`, so a credential can never leak across orgs via a shared/cached
    client.
    """

    def __init__(self, token: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=10.0,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def create_contact(self, properties: dict[str, str]) -> httpx.Response:
        response = await self._client.post(
            "/crm/v3/objects/contacts", json={"properties": properties},
        )
        # 409 (duplicate contact) and other 4xx are handled by the caller, not
        # retried; only rate-limit/server errors are worth a retry here.
        if response.status_code in _RETRYABLE_STATUS:
            response.raise_for_status()
        return response

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def search_contacts(self, query: str, limit: int) -> httpx.Response:
        response = await self._client.post(
            "/crm/v3/objects/contacts/search",
            json={
                "query": query,
                "limit": limit,
                "properties": ["email", "firstname", "lastname"],
            },
        )
        if response.status_code in _RETRYABLE_STATUS:
            response.raise_for_status()
        return response


async def _resolve_token(vault: CredentialVault, org_id: str) -> str:
    try:
        return await vault.retrieve(org_id, _PROVIDER)
    except CredentialNotFoundError as exc:
        raise ToolExecutionError(
            f"HubSpot is not connected for org {org_id!r}. Connect a HubSpot "
            "API key in integration settings before using this tool."
        ) from exc


def _contact_from_api(raw: dict[str, Any]) -> HubSpotContact:
    props = raw.get("properties", {})
    return HubSpotContact(
        id=str(raw.get("id", "")),
        email=props.get("email"),
        first_name=props.get("firstname"),
        last_name=props.get("lastname"),
    )


def build_hubspot_create_contact_tool(vault: CredentialVault) -> ToolDefinition:
    async def _handle(
        inp: HubSpotCreateContactIn, ctx: ToolContext
    ) -> HubSpotCreateContactOut:
        token = await _resolve_token(vault, ctx.org_id)
        properties: dict[str, str] = {"email": inp.email}
        if inp.first_name:
            properties["firstname"] = inp.first_name
        if inp.last_name:
            properties["lastname"] = inp.last_name
        if inp.company:
            properties["company"] = inp.company

        client = HubSpotClient(token)
        try:
            response = await client.create_contact(properties)
            if response.status_code == 409:
                search_response = await client.search_contacts(inp.email, 1)
                search_response.raise_for_status()
                results = search_response.json().get("results", [])
                if results:
                    return HubSpotCreateContactOut(
                        contact_id=str(results[0]["id"]), created=False,
                    )
                raise ToolExecutionError(
                    f"HubSpot reported a duplicate contact for {inp.email!r} "
                    "but it could not be found by search."
                )
            response.raise_for_status()
            body = response.json()
            return HubSpotCreateContactOut(contact_id=str(body["id"]), created=True)
        except httpx.HTTPStatusError as exc:
            raise ToolExecutionError(
                f"HubSpot API error creating contact: "
                f"{exc.response.status_code} {exc.response.text}"
            ) from exc
        finally:
            await client.aclose()

    return ToolDefinition(
        tool_id="integration.hubspot_create_contact",
        name="HubSpot: Create Contact",
        description=(
            "Create (or resolve an existing) HubSpot contact for this organization's "
            "connected HubSpot account."
        ),
        input_schema=HubSpotCreateContactIn,
        output_schema=HubSpotCreateContactOut,
        category="integration",
        handler=_handle,
    )


def build_hubspot_search_contacts_tool(vault: CredentialVault) -> ToolDefinition:
    async def _handle(
        inp: HubSpotSearchContactsIn, ctx: ToolContext
    ) -> HubSpotSearchContactsOut:
        token = await _resolve_token(vault, ctx.org_id)
        client = HubSpotClient(token)
        try:
            response = await client.search_contacts(inp.query, inp.limit)
            response.raise_for_status()
            results = response.json().get("results", [])
            return HubSpotSearchContactsOut(
                contacts=[_contact_from_api(r) for r in results],
            )
        except httpx.HTTPStatusError as exc:
            raise ToolExecutionError(
                f"HubSpot API error searching contacts: "
                f"{exc.response.status_code} {exc.response.text}"
            ) from exc
        finally:
            await client.aclose()

    return ToolDefinition(
        tool_id="integration.hubspot_search_contacts",
        name="HubSpot: Search Contacts",
        description=(
            "Search this organization's connected HubSpot account for contacts "
            "matching a free-text query."
        ),
        input_schema=HubSpotSearchContactsIn,
        output_schema=HubSpotSearchContactsOut,
        category="integration",
        handler=_handle,
    )
