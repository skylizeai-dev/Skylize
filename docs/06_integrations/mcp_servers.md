# Integration: MCP Servers

**Status:** Integration adapter spec (source of truth for MCP tools)
**Owner:** `director_agent_infrastructure` · `chief_security_officer` · `director_backend`
**Related:** [anthropic.md](./anthropic.md) · [n8n.md](./n8n.md) · [../03_agents/agent_governance.md §6](../03_agents/agent_governance.md#6-tool-manifest-standard) · [../architecture/03_agent_runtime.md §5](../architecture/03_agent_runtime.md#5-tool-proxy-if-tool) · [../02_architecture/system_boundaries.md §4.6](../02_architecture/system_boundaries.md#46-integration-boundary--interfaces-if-tool-if-integration)

---

## 1. Purpose

The Model Context Protocol (MCP) lets Skylize expose external tools/data sources
to agents in a standard way. This spec defines how MCP servers are integrated
**without breaking the zero-trust agent model**: an MCP tool is just another tool
behind the tool proxy and governance token — never a hole in the sandbox.

## 2. Architectural role

- Each MCP server is reached **only through the tool proxy** (`IF-TOOL`) and a
  Skylize **MCP adapter** at `IF-INTEGRATION` — agents do not open MCP connections
  themselves (no egress from the sandbox,
  [../architecture/03_agent_runtime.md §4](../architecture/03_agent_runtime.md#4-the-agent-sandbox-if-agent)).
- An MCP tool must appear in the agent's `allowed_tools` manifest as a `ToolGrant`
  to be reachable; a tool not in the manifest is a `governance.scope_violation`
  ([../03_agents/agent_governance.md §6](../03_agents/agent_governance.md#6-tool-manifest-standard)).

```
agent ──tool call──▶ tool proxy (validate token: sig→exp→rev→scope→budget→deleg)
                         └──▶ MCP adapter ──▶ MCP server (creds held by adapter)
```

## 3. Authentication & secrets

- MCP server credentials/endpoints live in the secrets manager, read only by the
  MCP adapter; never exposed to agents.
- Each MCP server is **allow-listed** per tenant; an agent cannot reach an
  un-listed server.

## 4. Trust model (MCP-specific risks)

MCP servers can return content that influences agent reasoning, so they are a
prompt-injection surface:
- Returned content is treated as **untrusted input**; security agents
  (`prompt_injection_agent`, `llm_safety_agent`) screen high-risk flows and
  `fail_closed` on suspicion.
- An MCP tool that performs a side effect still routes any resulting business
  action through the event/Decision-Engine path — MCP cannot bypass governance.
- Scope per `ToolGrant` is minimal and audited (`purpose` recorded).

## 5. Tenant isolation

MCP tool grants and server allow-lists are per `org_id`; an agent can invoke only
its tenant's MCP servers. Results carry `org_id` provenance.

## 6. Failure handling

- Server error/timeout → normalized error → agent `failure_mode`.
- Suspicious/anomalous MCP output → security screen → `fail_closed` + HITL
  (`SECURITY_SEVERITY_HIGH`) where warranted.
- Repeated violations via an MCP tool feed circuit-breaker conditions.

## 7. Events & observability

- Each MCP call is audited (`audit.action_recorded`) with the `ToolGrant.purpose`
  and `governance_token_id`; cost (if LLM-backed) recorded in Langfuse.

## 8. Ownership & evolution

- **Owner:** `director_agent_infrastructure` (MCP integration),
  `chief_security_officer` (trust/injection review), `director_backend` (adapter).
- **Evolution:** new MCP servers are added by allow-list + adapter config and a
  `ToolGrant` in the relevant contracts; the "MCP behind proxy + token, untrusted
  output, cannot bypass governance" invariants are permanent.
