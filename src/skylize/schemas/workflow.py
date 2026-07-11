"""Workflow graph schema — GraphSpec, NodeSpec, EdgeSpec.

The canonical in-memory representation of a workflow definition.
Stored as `spec_json` in the workflow_definitions table (DAL serialises to/from
this model).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class NodeSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    node_type: str  # transform | judge | llm | ...
    config: dict[str, Any] = {}
    judge: bool = False


class EdgeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str


class GraphSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_point: str
    nodes: list[NodeSpec]
    edges: list[EdgeSpec]
