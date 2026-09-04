"""Built-in internal tools + the default registry factory.

External integrations (HubSpot, search providers) resolve their own
credentials/config at the composition root (bootstrap.py) and are passed in
as already-constructed ports/vaults — this module only assembles the
`ToolDefinition` list, it never reaches for env vars or drivers itself.
"""

from __future__ import annotations

from ...app.credentials.oauth import OAuthCredentialService
from ...app.credentials.vault import CredentialVault
from ..base import ToolDefinition
from ..registry import ToolRegistry
from .asana_tools import (
    build_asana_add_project_member_tool,
    build_asana_create_project_tool,
    build_asana_create_task_tool,
)
from ...dal.gcp_wif import GcpWifRepository
from .datetime_tool import CURRENT_DATETIME_TOOL
from .gcp_tools import build_gcp_tools
from .drive_tools import build_drive_create_file_tool, build_drive_share_file_tool
from .hubspot_tools import build_hubspot_create_contact_tool, build_hubspot_search_contacts_tool
from .notion_tools import (
    build_notion_append_blocks_tool,
    build_notion_create_database_tool,
    build_notion_create_page_tool,
)
from .memory_recall import MemoryRecallPort, NullMemoryRecallPort, build_memory_recall_tool
from .web_search import NullWebSearchPort, WebSearchPort, build_web_search_tool


def build_builtin_tools(
    memory_recall_port: MemoryRecallPort | None = None,
    web_search_port: WebSearchPort | None = None,
    credential_vault: CredentialVault | None = None,
    oauth_credentials: OAuthCredentialService | None = None,
    wif_repo: "GcpWifRepository | None" = None,
    gcp_executor_factory: object | None = None,
) -> list[ToolDefinition]:
    tools = [
        build_memory_recall_tool(memory_recall_port or NullMemoryRecallPort()),
        CURRENT_DATETIME_TOOL,
        build_web_search_tool(web_search_port or NullWebSearchPort()),
    ]
    if credential_vault is not None:
        tools.append(build_hubspot_create_contact_tool(credential_vault))
        tools.append(build_hubspot_search_contacts_tool(credential_vault))
    # Drive tools need the OAuth service, not the vault: their credential is a
    # refreshable org-level grant in `oauth_credentials`, not a static API key.
    if oauth_credentials is not None:
        tools.append(build_drive_create_file_tool(oauth_credentials))
        tools.append(build_drive_share_file_tool(oauth_credentials))
        # Asana (integration_inputs.md 2.6) rides the same OAuth service for the
        # same reason: an org-level refreshable grant, not a static API key.
        tools.append(build_asana_create_task_tool(oauth_credentials))
        tools.append(build_asana_create_project_tool(oauth_credentials))
        tools.append(build_asana_add_project_member_tool(oauth_credentials))
        # Notion (integration_inputs.md 2.7). Same org-level refreshable grant.
        tools.append(build_notion_create_page_tool(oauth_credentials))
        tools.append(build_notion_create_database_tool(oauth_credentials))
        tools.append(build_notion_append_blocks_tool(oauth_credentials))
    # GCP containment. NOT on the OAuth service: a Workload Identity Federation
    # trust is not a stored grant (migration 0024), so it has its own repository
    # and its own gate. Registers only when BOTH the federation store and an
    # executor factory are wired, so a deployment without GCP has no such tool in
    # the registry at all -- it cannot be resolved, granted, or invoked.
    tools.extend(build_gcp_tools(
        wif_repo=wif_repo, executor_factory=gcp_executor_factory,
    ))
    return tools


def default_tool_registry(
    memory_recall_port: MemoryRecallPort | None = None,
    web_search_port: WebSearchPort | None = None,
    credential_vault: CredentialVault | None = None,
    oauth_credentials: OAuthCredentialService | None = None,
    wif_repo: "GcpWifRepository | None" = None,
    gcp_executor_factory: object | None = None,
) -> ToolRegistry:
    registry = ToolRegistry(
        build_builtin_tools(
            memory_recall_port, web_search_port, credential_vault, oauth_credentials,
            wif_repo, gcp_executor_factory,
        )
    )
    registry.validate_schemas()
    return registry
