"""The tool-calling layer (IF-TOOL) — agent-facing capabilities.

An agent never imports an adapter directly (system_boundaries.md §4.3/§4.6):
it calls `ToolProxy.invoke(tool_id, ...)`, which resolves the tool from the
`ToolRegistry`, validates the caller's governance token via the existing
`contracts.token.validate_tool_call` pipeline, and only then dispatches to the
tool's handler.
"""

from __future__ import annotations
