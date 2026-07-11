from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, ConfigDict
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger(__name__)

# Sustained-rate floor: 1.1 s between requests (~0.9 rps), regardless of
# whether the Anytype side has rate-limiting disabled.
_SUSTAINED_INTERVAL = 1.1


class AnytypeObjectType(BaseModel):
    model_config = ConfigDict(extra="allow")
    key: str
    id: str = ""
    name: str = ""


class AnytypeProperty(BaseModel):
    model_config = ConfigDict(extra="allow")
    key: str
    format: str = ""
    date: str | None = None
    text: str | None = None


class AnytypeObject(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str = ""
    type: AnytypeObjectType
    snippet: str = ""
    properties: list[AnytypeProperty] = []


class AnytypeObjectDetail(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str = ""
    type: AnytypeObjectType
    markdown: str = ""
    properties: list[AnytypeProperty] = []


class _ListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    data: list[AnytypeObject] = []


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    return isinstance(exc, (httpx.ConnectError, httpx.TimeoutException))


class AnytypeClient:
    def __init__(self, api_key: str, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        self._last_call: float = 0.0

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < _SUSTAINED_INTERVAL:
            await asyncio.sleep(_SUSTAINED_INTERVAL - elapsed)
        self._last_call = time.monotonic()

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    async def _get(self, path: str) -> Any:
        await self._throttle()
        r = await self._client.get(path)
        r.raise_for_status()
        return r.json()

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        await self._throttle()
        r = await self._client.post(path, json=payload)
        r.raise_for_status()
        return r.json()

    async def list_objects(
        self,
        space_id: str,
        modified_since: str | None = None,  # noqa: ARG002 — filtering done by caller
    ) -> list[AnytypeObject]:
        raw = await self._get(f"/v1/spaces/{space_id}/objects")
        return _ListResponse.model_validate(raw).data

    async def get_object(self, space_id: str, object_id: str) -> AnytypeObjectDetail:
        raw = await self._get(f"/v1/spaces/{space_id}/objects/{object_id}")
        return AnytypeObjectDetail.model_validate(raw["object"])

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AnytypeClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
