"""Unit tests for the search.web tool — provider port wrapper + graceful fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from skylize.tools.base import ToolContext
from skylize.tools.builtin.web_search import (
    NullWebSearchPort,
    WebSearchHit,
    build_web_search_port,
    build_web_search_tool,
)

CTX = ToolContext(org_id="org_a", agent_id="research_agent", correlation_id=uuid4())


class _FakeSettings:
    def __init__(self, search_api_key: str = "", search_provider: str = "brave") -> None:
        self.search_api_key = search_api_key
        self.search_provider = search_provider


async def test_unconfigured_returns_graceful_result_not_exception() -> None:
    tool = build_web_search_tool(NullWebSearchPort())
    inp = tool.input_schema.model_validate({"query": "skylize"})
    out = await tool.handler(inp, CTX)

    assert out.status == "not_configured"
    assert out.results == []


async def test_configured_port_returns_real_shaped_results() -> None:
    port = AsyncMock()
    port.search.return_value = [
        WebSearchHit(title="Skylize", url="https://skylize.example", snippet="A platform."),
    ]
    tool = build_web_search_tool(port)

    inp = tool.input_schema.model_validate({"query": "skylize", "max_results": 3})
    out = await tool.handler(inp, CTX)

    port.search.assert_awaited_once_with(query="skylize", max_results=3)
    assert out.status == "ok"
    assert out.results == [
        WebSearchHit(title="Skylize", url="https://skylize.example", snippet="A platform."),
    ]


async def test_malformed_input_rejected_by_schema() -> None:
    tool = build_web_search_tool(NullWebSearchPort())
    with pytest.raises(ValidationError):
        tool.input_schema.model_validate({"query": "skylize", "max_results": 100})

    with pytest.raises(ValidationError):
        tool.input_schema.model_validate({"max_results": 5})  # missing required query


def test_build_web_search_port_falls_back_to_null_when_unconfigured() -> None:
    port = build_web_search_port(_FakeSettings(search_api_key=""))
    assert isinstance(port, NullWebSearchPort)


def test_build_web_search_port_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unknown search_provider"):
        build_web_search_port(_FakeSettings(search_api_key="key", search_provider="not-a-provider"))


def test_tool_id_and_category_are_stable() -> None:
    tool = build_web_search_tool(NullWebSearchPort())
    assert tool.tool_id == "search.web"
    assert tool.category == "search"
