"""Verify the Anthropic tool-use wire format translation.

The `anthropic` package is not installed in this environment (no API key yet
— see adapters/llm/anthropic_adapter.py's deferred SDK import), so
`AnthropicAdapter` cannot be instantiated here. These tests exercise the pure,
SDK-independent translation helpers directly against Anthropic's documented
request/response shapes:
  - request:  tools=[{"name","description","input_schema"}],
              messages=[{"role","content":[{"type":...}]}]
  - response: content blocks of type "text" / "tool_use"
  - tool result turn: {"type":"tool_result","tool_use_id":...,"content":...}
"""

from __future__ import annotations

from types import SimpleNamespace

from skylize.adapters.llm.anthropic_adapter import (
    _normalize_anthropic_message,
    _sanitize_tool_name,
    _to_anthropic_message,
    _to_anthropic_tools,
)
from skylize.adapters.llm.gateway import LLMContentBlock, LLMMessage
from skylize.tools.builtin import build_builtin_tools
from skylize.tools.builtin.memory_recall import NullMemoryRecallPort


def test_sanitize_tool_name_strips_dots() -> None:
    assert _sanitize_tool_name("memory.search") == "memory_search"
    assert _sanitize_tool_name("utility.current_datetime") == "utility_current_datetime"


def test_to_anthropic_tools_matches_documented_shape() -> None:
    tools = build_builtin_tools(NullMemoryRecallPort())
    payload, name_map = _to_anthropic_tools(tools)

    assert len(payload) == len(tools) == 3
    for entry in payload:
        assert set(entry.keys()) == {"name", "description", "input_schema"}
        assert isinstance(entry["description"], str) and entry["description"]
        assert entry["input_schema"]["type"] == "object"
        # Anthropic tool names must match ^[a-zA-Z0-9_-]{1,128}$ — no dots.
        assert "." not in entry["name"]

    memory_entry = next(e for e in payload if name_map[e["name"]] == "memory.search")
    assert memory_entry["name"] == "memory_search"
    assert "query" in memory_entry["input_schema"]["properties"]
    assert name_map == {
        "memory_search": "memory.search",
        "utility_current_datetime": "utility.current_datetime",
        "search_web": "search.web",
    }


def test_to_anthropic_message_text_block() -> None:
    message = LLMMessage(role="user", content=[LLMContentBlock(kind="text", text="hello")])
    assert _to_anthropic_message(message) == {
        "role": "user",
        "content": [{"type": "text", "text": "hello"}],
    }


def test_to_anthropic_message_tool_use_block_sanitizes_name() -> None:
    message = LLMMessage(
        role="assistant",
        content=[
            LLMContentBlock(
                kind="tool_use", tool_use_id="toolu_1",
                tool_name="memory.search", tool_input={"query": "voice"},
            )
        ],
    )
    assert _to_anthropic_message(message) == {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "memory_search", "input": {"query": "voice"}}
        ],
    }


def test_to_anthropic_message_tool_result_block() -> None:
    message = LLMMessage(
        role="user",
        content=[
            LLMContentBlock(kind="tool_result", tool_use_id="toolu_1", tool_output='{"hits": []}'),
        ],
    )
    assert _to_anthropic_message(message) == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"hits": []}'}],
    }


def test_to_anthropic_message_tool_result_error_sets_is_error() -> None:
    message = LLMMessage(
        role="user",
        content=[
            LLMContentBlock(kind="tool_result", tool_use_id="toolu_1", tool_output="boom", is_error=True),
        ],
    )
    result = _to_anthropic_message(message)["content"][0]
    assert result["is_error"] is True


def test_normalize_anthropic_message_restores_original_tool_id() -> None:
    name_map = {"memory_search": "memory.search"}
    fake_message = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="Let me check memory first."),
            SimpleNamespace(type="tool_use", id="toolu_1", name="memory_search", input={"query": "voice"}),
        ]
    )
    text, blocks = _normalize_anthropic_message(fake_message, name_map=name_map)

    assert text == "Let me check memory first."
    assert blocks[0] == LLMContentBlock(kind="text", text="Let me check memory first.")
    assert blocks[1] == LLMContentBlock(
        kind="tool_use", tool_use_id="toolu_1", tool_name="memory.search", tool_input={"query": "voice"},
    )


def test_normalize_anthropic_message_pure_text_has_no_tool_blocks() -> None:
    fake_message = SimpleNamespace(content=[SimpleNamespace(type="text", text="final answer")])
    text, blocks = _normalize_anthropic_message(fake_message, name_map={})
    assert text == "final answer"
    assert len(blocks) == 1 and blocks[0].kind == "text"
