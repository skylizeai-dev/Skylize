"""Built-in internal tools + the default registry factory.

External integrations (HubSpot, search providers) resolve their own
credentials/config at the composition root (bootstrap.py) and are passed in
as already-constructed ports/vaults — this module only assembles the
`ToolDefinition` list, it never reaches for env vars or drivers itself.
"""

from __future__ import annotations

from ...app.credentials.vault import CredentialVault
from ..base import ToolDefinition
from ..registry import ToolRegistry
from .datetime_tool import CURRENT_DATETIME_TOOL
from .hubspot_tools import build_hubspot_create_contact_tool, build_hubspot_search_contacts_tool
from .memory_recall import MemoryRecallPort, NullMemoryRecallPort, build_memory_recall_tool
from .web_search import NullWebSearchPort, WebSearchPort, build_web_search_tool


def build_builtin_tools(
    memory_recall_port: MemoryRecallPort | None = None,
    web_search_port: WebSearchPort | None = None,
    credential_vault: CredentialVault | None = None,
) -> list[ToolDefinition]:
    tools = [
        build_memory_recall_tool(memory_recall_port or NullMemoryRecallPort()),
        CURRENT_DATETIME_TOOL,
        build_web_search_tool(web_search_port or NullWebSearchPort()),
    ]
    if credential_vault is not None:
        tools.append(build_hubspot_create_contact_tool(credential_vault))
        tools.append(build_hubspot_search_contacts_tool(credential_vault))
    return tools


def default_tool_registry(
    memory_recall_port: MemoryRecallPort | None = None,
    web_search_port: WebSearchPort | None = None,
    credential_vault: CredentialVault | None = None,
) -> ToolRegistry:
    registry = ToolRegistry(build_builtin_tools(memory_recall_port, web_search_port, credential_vault))
    registry.validate_schemas()
    return registry
