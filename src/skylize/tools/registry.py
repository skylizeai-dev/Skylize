"""The tool registry — in-memory, fail-closed on unknown tool_id.

Mirrors `contracts/registry.py`'s `AgentRegistry`: a flat cache keyed by id,
duplicate registration rejected at construction, schemas checked once at
startup so a broken tool never surfaces mid-run.
"""

from __future__ import annotations

from .base import ToolDefinition, ToolNotRegistered


class ToolSchemaError(Exception):
    """A tool's input/output schema cannot produce a JSON Schema."""


class ToolRegistry:
    def __init__(self, tools: list[ToolDefinition] | None = None) -> None:
        self._cache: dict[str, ToolDefinition] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: ToolDefinition) -> None:
        if tool.tool_id in self._cache:
            raise ValueError(f"duplicate tool_id in registry: {tool.tool_id}")
        self._cache[tool.tool_id] = tool

    def resolve(self, tool_id: str) -> ToolDefinition:
        """Resolve a tool; fail closed on unknown tool_id."""
        tool = self._cache.get(tool_id)
        if tool is None:
            raise ToolNotRegistered(
                f"tool_id={tool_id!r} is not registered; unknown tools fail closed"
            )
        return tool

    def has(self, tool_id: str) -> bool:
        return tool_id in self._cache

    def all(self) -> list[ToolDefinition]:
        return list(self._cache.values())

    def tool_ids(self) -> list[str]:
        return list(self._cache.keys())

    def validate_schemas(self) -> None:
        """Assert every tool's input/output schema resolves to JSON Schema."""
        for tool in self._cache.values():
            try:
                tool.input_schema.model_json_schema()
                tool.output_schema.model_json_schema()
            except Exception as exc:  # noqa: BLE001 — normalize to one error type
                raise ToolSchemaError(
                    f"tool_id={tool.tool_id!r} has an unresolvable schema: {exc}"
                ) from exc
