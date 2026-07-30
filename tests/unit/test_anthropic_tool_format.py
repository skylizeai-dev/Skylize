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

import json
from uuid import uuid4

from skylize.adapters.llm.anthropic_adapter import (
    _normalize_anthropic_message,
    _sanitize_tool_name,
    _to_anthropic_message,
    _to_anthropic_tools,
    _tools_input_chars,
)
from skylize.adapters.llm.gateway import (
    LLMContentBlock,
    LLMGenerateWithToolsRequest,
    LLMMessage,
)
from skylize.adapters.llm.spend_ceiling import estimate_input_tokens
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


# ---------------------------------------------------------------------------
# P5 — the tool schemas are built ONCE per turn, and the character count that
# feeds the spend-ceiling estimate is byte-identical to the previous formula.
#
# _tools_input_chars used to call model_json_schema() for every tool, and
# _to_anthropic_tools called it again for the same tools two lines later
# (Pydantic v2 does not memoize it), so every tool-loop iteration built every
# schema twice and serialized one copy purely to obtain a character count.
# ---------------------------------------------------------------------------


def _request(**kw: object) -> LLMGenerateWithToolsRequest:
    defaults: dict[str, object] = {
        "model": "default",
        "messages": [
            LLMMessage(role="user", content=[LLMContentBlock(kind="text", text="hello")])
        ],
        "requested_max_tokens": 100,
        "governance_token_id": uuid4(),
        "org_id": "org_test",
        "correlation_id": uuid4(),
        "agent_id": "agent_test",
        "system": "You are a helpful assistant.",
    }
    defaults.update(kw)
    return LLMGenerateWithToolsRequest(**defaults)  # type: ignore[arg-type]


def _previous_formula(request: LLMGenerateWithToolsRequest, tools: list) -> int:
    """The EXACT pre-change body of _tools_input_chars, kept here as the oracle.

    It regenerates every schema itself — which is the duplicated work — so the
    new implementation is compared against what it replaced, not against a
    paraphrase of it.
    """
    total = len(request.system or "")
    for message in request.messages:
        for block in message.content:
            total += len(block.text or "")
            total += len(block.tool_output or "")
            if block.tool_input:
                total += len(json.dumps(block.tool_input, default=str))
    for tool in tools:
        total += len(tool.tool_id) + len(tool.description)
        total += len(json.dumps(tool.input_schema.model_json_schema(), default=str))
    return total


def test_tools_input_chars_is_byte_identical_to_the_previous_formula() -> None:
    tools = build_builtin_tools(NullMemoryRecallPort())
    request = _request()
    anthropic_tools, _ = _to_anthropic_tools(tools)

    assert _tools_input_chars(request, tools, anthropic_tools) == _previous_formula(
        request, tools
    )


def test_tools_input_chars_unchanged_across_realistic_message_shapes() -> None:
    """Text, tool_result and tool_use blocks all contribute; none may drift."""
    tools = build_builtin_tools(NullMemoryRecallPort())
    anthropic_tools, _ = _to_anthropic_tools(tools)
    shapes: list[LLMGenerateWithToolsRequest] = [
        _request(system=None),
        _request(messages=[]),
        _request(
            messages=[
                LLMMessage(
                    role="assistant",
                    content=[
                        LLMContentBlock(
                            kind="tool_use",
                            tool_use_id="tu_1",
                            tool_name="memory.search",
                            tool_input={"query": "quarterly targets", "limit": 5},
                        )
                    ],
                ),
                LLMMessage(
                    role="user",
                    content=[
                        LLMContentBlock(
                            kind="tool_result",
                            tool_use_id="tu_1",
                            tool_output='{"hits": ["a", "b"]}',
                        )
                    ],
                ),
            ]
        ),
    ]
    for request in shapes:
        assert _tools_input_chars(request, tools, anthropic_tools) == _previous_formula(
            request, tools
        )


def test_each_schema_is_built_once_per_turn(monkeypatch) -> None:
    """One model_json_schema() call per tool per turn, where there used to be two.

    Both arrangements are counted in the same test, so the "was 2, now 1" claim
    is measured rather than asserted: `_previous_formula` is a verbatim copy of
    the old `_tools_input_chars` body, so pairing it with `_to_anthropic_tools`
    reproduces exactly what the old call site did.
    """
    tools = build_builtin_tools(NullMemoryRecallPort())
    calls: list[str] = []

    for tool in tools:
        model = tool.input_schema
        original = model.model_json_schema
        name = model.__name__

        def counting(*args: object, _orig=original, _name=name, **kwargs: object):
            calls.append(_name)
            return _orig(*args, **kwargs)

        monkeypatch.setattr(model, "model_json_schema", counting)

    request = _request()

    # OLD arrangement: estimate first (rebuilding every schema), payload after.
    calls.clear()
    _previous_formula(request, tools)
    _to_anthropic_tools(tools)
    assert len(calls) == 2 * len(tools), (
        f"the old arrangement should build {2 * len(tools)} schemas; got {len(calls)}"
    )

    # NEW arrangement: payload once, estimate measured from it.
    calls.clear()
    anthropic_tools, _ = _to_anthropic_tools(tools)
    _tools_input_chars(request, tools, anthropic_tools)
    assert len(calls) == len(tools), (
        f"expected one schema build per tool, got {len(calls)} for {len(tools)} "
        f"tools: {calls}"
    )
    assert sorted(calls) == sorted(set(calls)), f"a schema was built twice: {calls}"


def test_the_estimate_never_falls_below_the_previous_one() -> None:
    """The count feeds the spend-ceiling gate, where UNDER-counting is the unsafe
    direction: it would let a breaching call through. Equality is what this
    change guarantees; this pins the inequality that must never be violated."""
    tools = build_builtin_tools(NullMemoryRecallPort())
    request = _request()
    anthropic_tools, _ = _to_anthropic_tools(tools)

    new_chars = _tools_input_chars(request, tools, anthropic_tools)
    old_chars = _previous_formula(request, tools)
    assert new_chars >= old_chars
    # ...and the token estimate the gate actually consumes is likewise unmoved.
    assert estimate_input_tokens(new_chars) == estimate_input_tokens(old_chars)


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
