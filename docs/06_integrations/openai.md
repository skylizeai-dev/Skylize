# Integration: OpenAI (via LLM Gateway)

**Status:** Integration adapter spec (source of truth for this adapter)
**Owner:** `director_agent_infrastructure` · `director_backend`
**Related:** [anthropic.md](./anthropic.md) · [mcp_servers.md](./mcp_servers.md) · [../architecture/01_final_stack.md §4.8](../architecture/01_final_stack.md#48-llm-access--provider-abstracted-gateway) · [../02_architecture/system_boundaries.md §4.6](../02_architecture/system_boundaries.md#46-integration-boundary--interfaces-if-tool-if-integration)

---

## 1. Purpose

OpenAI is one provider behind the **provider-abstracted LLM gateway**, alongside
Anthropic. Gemini is a planned third provider — not yet integrated, no Google
SDK dependency in `pyproject.toml`. Agents never call OpenAI directly; the gateway routes
`llm.generate` to the OpenAI adapter when policy/config selects it (e.g. for cost,
capability, or failover). This spec defines that adapter; it is structurally
identical to [anthropic.md](./anthropic.md) by design — that sameness is the point
of the abstraction.

## 2. Architectural role

- At `IF-INTEGRATION`: sole egress + credentials.
- Reached only via the tool proxy (`IF-TOOL`) after token validation.
- Provider name appears **only** in this adapter, never in agent/domain code.

## 3. Authentication & secrets

- API key in the secrets manager / Vault; read only by this adapter; never in
  code/logs/events/memory.

## 4. Inward contract (`IF-TOOL`)

`ToolGrant(tool_id="llm.generate")`; proxy enforces `max_calls_per_run` and the
token's `max_token_budget` before dispatch. The gateway hands the adapter a
provider-neutral request.

## 5. Outward contract (`IF-INTEGRATION`)

- Maps the normalized request to the OpenAI Chat Completions / Responses API per
  the gateway's model policy.
- Enforces the per-run token budget ceiling; over-budget calls refused before
  egress.
- Normalizes the response to the gateway's provider-neutral shape (so callers
  cannot tell which provider served the request).

## 6. Events & observability

- Cost/quality in **Langfuse keyed by `governance_token_id`**.
- `audit.action_recorded` per call; business events are wrapped by the
  Orchestrator from the agent's output, not emitted by the adapter.

## 7. Failure handling

- Error/timeout → normalized error → agent `failure_mode`.
- Outage → gateway **failover** to Anthropic per policy. Gemini is a roadmap
  failover target — not available until the adapter and SDK dependency exist.
- Budget overrun attempts feed circuit-breaker conditions.

## 8. Data & privacy

- Prompt/response transit only; OpenAI is not a system of record; sensitive
  content gated by `brand_legal`/`data_access` policies before egress.

## 9. Ownership & evolution

- **Owner:** `director_agent_infrastructure` (gateway), `director_backend`
  (adapter).
- **Evolution:** add/drop OpenAI behind the `LLMGateway` port with zero agent
  impact; model and routing are policy/config. Multiple providers behind one
  gateway is exactly what enables cost routing and failover at Scale.
