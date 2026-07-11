"""Unit tests for ToolRegistry — registration, dedup, fail-closed resolve."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from skylize.tools.base import ToolContext, ToolDefinition, ToolNotRegistered
from skylize.tools.builtin import build_builtin_tools
from skylize.tools.registry import ToolRegistry, ToolSchemaError


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


async def _handle(inp: _In, ctx: ToolContext) -> _Out:
    return _Out(value=inp.value)


def _tool(tool_id: str = "test.tool") -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id, name="Test Tool", description="A test tool.",
        input_schema=_In, output_schema=_Out, category="compute", handler=_handle,
    )


def test_register_and_resolve() -> None:
    registry = ToolRegistry([_tool()])
    resolved = registry.resolve("test.tool")
    assert resolved.tool_id == "test.tool"


def test_duplicate_tool_id_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="duplicate tool_id"):
        ToolRegistry([_tool(), _tool()])


def test_duplicate_tool_id_rejected_on_register() -> None:
    registry = ToolRegistry([_tool()])
    with pytest.raises(ValueError, match="duplicate tool_id"):
        registry.register(_tool())


def test_unknown_tool_id_fails_closed() -> None:
    registry = ToolRegistry([_tool()])
    with pytest.raises(ToolNotRegistered):
        registry.resolve("does_not_exist")


def test_has_and_tool_ids_and_all() -> None:
    registry = ToolRegistry([_tool("a.one"), _tool("a.two")])
    assert registry.has("a.one")
    assert not registry.has("a.three")
    assert set(registry.tool_ids()) == {"a.one", "a.two"}
    assert {t.tool_id for t in registry.all()} == {"a.one", "a.two"}


def test_validate_schemas_passes_for_well_formed_tools() -> None:
    ToolRegistry([_tool()]).validate_schemas()  # no raise


def test_validate_schemas_rejects_unresolvable_schema() -> None:
    class _Lock:
        pass

    class _BadIn(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)
        blob: _Lock

    bad_tool = ToolDefinition(
        tool_id="bad.tool", name="Bad Tool", description="Unresolvable schema.",
        input_schema=_BadIn, output_schema=_Out, category="compute", handler=_handle,
    )
    with pytest.raises(ToolSchemaError):
        ToolRegistry([bad_tool]).validate_schemas()


def test_builtin_tools_are_registerable_and_schema_valid() -> None:
    registry = ToolRegistry(build_builtin_tools())
    registry.validate_schemas()
    assert registry.tool_ids() == ["memory.search", "utility.current_datetime", "search.web"]
