"""search.web — public web search behind a swappable provider port.

Mirrors `memory_recall.py`'s port pattern: the tool depends on the narrow
`WebSearchPort` protocol, not on any concrete search API, and degrades
gracefully (`NullWebSearchPort`) when no provider is configured
(`SKYLIZE_SEARCH_API_KEY` unset) — same shape as `Container.knowledge_ingestion`
being `None` under the analogous condition (bootstrap.py).

Anthropic's Messages API does not yet expose a server-side web_search tool
through this codebase's adapter (`anthropic_adapter.py` only builds
client-side `tool_use` definitions), so this is a client-side adapter. Brave
Search is the first provider (free tier, simple bearer-style header auth);
`build_web_search_port` is the single switch point for adding another.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ..base import ToolContext, ToolDefinition

if TYPE_CHECKING:
    from ...config import Settings


class WebSearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    max_results: int = Field(default=5, gt=0, le=20)


class WebSearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    url: str
    snippet: str = ""


class WebSearchOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "ok"  # "ok" | "not_configured"
    results: list[WebSearchHit] = Field(default_factory=list)
    message: str = ""


class WebSearchPort(Protocol):
    """What the tool needs from a search backend: query in, hits out."""

    async def search(self, *, query: str, max_results: int) -> list[WebSearchHit]: ...


class NullWebSearchPort:
    """No provider configured (no SKYLIZE_SEARCH_API_KEY)."""

    async def search(self, *, query: str, max_results: int) -> list[WebSearchHit]:
        return []


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    return isinstance(exc, (httpx.ConnectError, httpx.TimeoutException))


class BraveSearchPort:
    """Real backend: Brave Search API (https://api.search.brave.com)."""

    _BASE_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def search(self, *, query: str, max_results: int) -> list[WebSearchHit]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                self._BASE_URL,
                params={"q": query, "count": max_results},
                headers={"Accept": "application/json", "X-Subscription-Token": self._api_key},
            )
            response.raise_for_status()
            payload = response.json()

        raw_results = payload.get("web", {}).get("results", [])
        return [
            WebSearchHit(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=str(item.get("description", "")),
            )
            for item in raw_results[:max_results]
        ]


def build_web_search_port(settings: "Settings") -> WebSearchPort:
    """The single switch point for adding another search provider."""
    if not settings.search_api_key:
        return NullWebSearchPort()
    if settings.search_provider == "brave":
        return BraveSearchPort(settings.search_api_key)
    raise ValueError(f"unknown search_provider: {settings.search_provider!r}")


def build_web_search_tool(port: WebSearchPort) -> ToolDefinition:
    is_configured = not isinstance(port, NullWebSearchPort)

    async def _handle(inp: WebSearchIn, _: ToolContext) -> WebSearchOut:
        if not is_configured:
            return WebSearchOut(
                status="not_configured",
                results=[],
                message="web search is not configured; no SKYLIZE_SEARCH_API_KEY is set.",
            )
        hits = await port.search(query=inp.query, max_results=inp.max_results)
        return WebSearchOut(status="ok", results=hits)

    return ToolDefinition(
        tool_id="search.web",
        name="Web Search",
        description="Search the public web for current information.",
        input_schema=WebSearchIn,
        output_schema=WebSearchOut,
        category="search",
        handler=_handle,
    )
