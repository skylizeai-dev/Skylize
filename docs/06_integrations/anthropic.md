# Integration: Anthropic (via LLM Gateway)

**Status:** Integration adapter spec (source of truth for this adapter)
**Owner:** `director_agent_infrastructure` · `director_backend`
**Related:** [openai.md](./openai.md) · [mcp_servers.md](./mcp_servers.md) · [../architecture/01_final_stack.md §4.8](../architecture/01_final_stack.md#48-llm-access--provider-abstracted-gateway) · [../02_architecture/system_boundaries.md §4.6](../02_architecture/system_boundaries.md#46-integration-boundary--interfaces-if-tool-if-integration) · [../03_agents/agent_governance.md §4](../03_agents/agent_governance.md#4-governance-token)

---

## 1. Purpose

Anthropic (Claude) is one provider behind the **provider-abstracted LLM
gateway**. Agents never call Anthropic directly; they request `llm.generate`
through the tool proxy, and the gateway routes to the Anthropic adapter when
policy/config selects it. This spec defines that adapter.

## 2. Architectural role

- Lives at the **Integration Boundary** (`IF-INTEGRATION`), the only place with
  egress and credentials.
- Reached only via the **tool proxy** (`IF-TOOL`) after governance-token
  validation (signature→expiry→revocation→scope→budget→delegation).
- The provider name **never appears in agent or domain code** — only in this
  adapter ([../architecture/01_final_stack.md §6](../architecture/01_final_stack.md#6-anti-lock-in-guarantees-invariants)).

## 3. Authentication & secrets

- API key held in the **secrets manager / Vault**; read only by this adapter.
- Never in code, env-in-VCS, logs, events, or memory
  ([../architecture/05_security_architecture.md §7](../architecture/05_security_architecture.md#7-secrets-management)).

## 4. Inward contract (`IF-TOOL`)

The agent's tool grant is `ToolGrant(tool_id="llm.generate", ...)`. The proxy
enforces `max_calls_per_run` and that the call fits the token's `max_token_budget`
**before** dispatch. The gateway normalizes the request (model-agnostic prompt +
params) and selects the provider/model per tenant policy and cost routing.

## 5. Outward contract (`IF-INTEGRATION`)

- Maps the normalized request to the Anthropic Messages API; uses the latest
  appropriate Claude model per the gateway's model policy.
- Enforces the per-run **token budget ceiling**; a call exceeding budget is
  refused before egress and the agent's `escalation_path`/`failure_mode` applies.
- Normalizes the response back to the gateway's provider-neutral shape.

## 6. Events & observability

- Cost/quality recorded in **Langfuse keyed by `governance_token_id`**
  ([../08_operations/observability.md §4](../08_operations/observability.md#4-llm-observability-langfuse)).
- The triggering agent output is wrapped as the appropriate event by the
  Orchestrator (e.g. `creative.hooks_generated`) — the adapter itself emits cost
  telemetry + `audit.action_recorded`, never business events directly.

## 7. Failure handling

- Provider error/timeout → adapter returns a normalized error; the agent's
  `failure_mode` applies (creative workers `fallback_degraded`; security workers
  `fail_closed`).
- Provider outage → the gateway can **fail over** to another provider (OpenAI/
  Gemini) per policy at Scale ([../architecture/06_deployment_architecture.md §6](../architecture/06_deployment_architecture.md#6-migration-triggers-mvp--scale)).
- Budget overrun attempts contribute to circuit-breaker trip conditions
  ([../03_agents/agent_governance.md §7](../03_agents/agent_governance.md#7-circuit-breaker-rules)).

## 8. Data & privacy

- Only the prompt/response transit through the provider; Anthropic is **not** a
  system of record. PII handling follows the data rules; sensitive content is
  governed by `brand_legal`/`data_access` policies before egress.

## 9. Ownership & evolution

- **Owner:** `director_agent_infrastructure` (gateway), `director_backend`
  (adapter).
- **Evolution:** adding/removing Anthropic is an adapter change behind the
  `LLMGateway` port; agents are unaffected. Model selection is policy/config, not
  code.
