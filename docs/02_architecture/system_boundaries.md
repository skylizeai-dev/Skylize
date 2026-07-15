# System Boundaries

**Status:** Foundation document (source of truth)
**Owner:** Chief Systems Architect
**Related:** [event_driven_architecture.md](./event_driven_architecture.md) · [agent_governance.md](../03_agents/agent_governance.md) · [agent_contract_registry.md](../03_agents/agent_contract_registry.md)

---

## 1. Purpose

This document defines the hard boundaries of the Skylize platform: what the
system **owns**, what **external systems own**, and the named interfaces that
mediate every crossing. A "boundary" here is a trust, data-ownership, and
failure-isolation perimeter. Nothing crosses a boundary except through a named
interface with an explicit contract.

The agent layer (see [agent_governance.md](../03_agents/agent_governance.md))
operates *inside* these boundaries and is never permitted to reach across them
directly. All outward action by an agent is mediated by the infrastructure
layer and emitted as events (see
[event_driven_architecture.md](./event_driven_architecture.md)).

---

## 2. Boundary Map (high level)

```
                         ┌──────────────────────────────────────────────┐
   Browser / API client  │                EDGE BOUNDARY                  │
   ───────────────────►  │  Cloudflare → API Gateway (FastAPI)           │
                         │  Auth (Clerk/Auth0)  ·  Rate limit  ·  WAF    │
                         └───────────────┬──────────────────────────────┘
                                         │  IF-EDGE
                         ┌───────────────▼──────────────────────────────┐
                         │             APPLICATION BOUNDARY              │
                         │  Service layer · Orchestrator · Decision      │
                         │  Engine · Governance Authority                │
                         └───┬─────────────┬───────────────┬────────────┘
              IF-AGENT       │             │ IF-DATA        │ IF-EVENT
                         ┌───▼───┐   ┌──────▼──────┐  ┌──────▼───────┐
                         │ AGENT │   │   DATA      │  │  EVENT BUS   │
                         │ LAYER │   │  BOUNDARY   │  │  (Redis      │
                         │ Lang  │   │ Postgres ·  │  │   Streams)   │
                         │ Graph │   │ Qdrant ·    │  └──────┬───────┘
                         │ (sbx) │   │ Redis · S3  │         │ IF-EVENT
                         └───┬───┘   └─────────────┘         │
                  IF-TOOL    │                               │
                         ┌───▼───────────────────────────────▼─────────┐
                         │            INTEGRATION BOUNDARY              │
                         │  n8n · Shopify · Stripe · Meta Ads · TikTok  │
                         │  LLM Providers (OpenAI/Anthropic/Gemini)     │
                         └──────────────────────────────────────────────┘
```

Five boundaries, six named interfaces: `IF-EDGE`, `IF-AGENT`, `IF-DATA`,
`IF-EVENT`, `IF-TOOL`, `IF-INTEGRATION`.

---

## 3. Ownership: Skylize vs. External

### 3.1 Skylize owns

| Asset | Boundary | Notes |
|---|---|---|
| Agent contracts & registry | Application | Source of truth for agent behavior |
| Governance authority & signing keys | Application | Root of trust for governance tokens |
| Decision Engine state | Application | Consumes/emits events |
| Event log (Redis Streams + cold archive) | Event | Full audit/replay record |
| Tenant business data (campaigns, creative, briefs, scores) | Data | Per-tenant isolated in Postgres |
| Vector memory (embeddings, semantic memory) | Data | Qdrant, namespaced per tenant |
| Operational cache & queues | Data | Redis |
| Generated creative assets | Data | S3, tenant-prefixed |
| Audit logs | Data | Append-only, immutable retention |

### 3.2 External systems own

| Asset | Owner | Skylize holds |
|---|---|---|
| Store catalog, orders, customers | Shopify | Scoped read mirror + webhooks |
| Payment & subscription state | Stripe | Billing reference IDs only; never PAN/card data |
| Ad account performance & spend | Meta Ads / TikTok | Read metrics + scoped write (campaign ops) |
| User identity & sessions | Clerk / Auth0 | `user_id`, `org_id`, verified claims only |
| Workflow execution (low-code automations) | n8n | Trigger contracts + signed callbacks |
| Model inference | OpenAI / Anthropic / Gemini | Prompt/response transit only; provider-abstracted |

**Rule:** Skylize never becomes the system of record for data an external system
owns. It holds *scoped, expiring mirrors* and *reference IDs*. Authority over
that data remains external.

---

## 4. The Boundaries in Detail

### 4.1 Edge Boundary — interface `IF-EDGE`

**Perimeter:** Cloudflare → FastAPI API Gateway.
**Owns:** TLS termination, WAF, DDoS protection, global rate limiting, request
authentication, request shaping.

**Contract (`IF-EDGE`):**
- Every inbound request carries a bearer token issued by Clerk/Auth0.
- The Gateway verifies the JWT signature against the IdP JWKS, extracts
  `user_id`, `org_id` (tenant), and roles, and stamps a verified
  `RequestContext`. Unverified requests never pass the edge.
- Rate limits are enforced per `org_id` and per route class.
- No business logic lives at the edge — it authenticates, throttles, and
  forwards.

**Rejection behavior:** On auth failure → `401`. On authorization failure →
`403`. On rate limit → `429` with `Retry-After`. On WAF block → `403` and a
`GovernanceEvent` (`type=governance.edge_block`) is emitted to the event bus
(see [event taxonomy](./event_driven_architecture.md#5-event-taxonomy)).
The request never reaches the Application Boundary.

### 4.2 Application Boundary — interfaces `IF-AGENT`, `IF-DATA`, `IF-EVENT`

**Perimeter:** Service layer, Orchestrator, Decision Engine, Governance
Authority. This is the brain of the platform and the **only** layer permitted
to mint governance tokens, resolve agent contracts, and authorize data access.

Responsibilities:
- **Orchestrator** — resolves agent contracts from the registry
  (see [registry lookup pattern](../03_agents/agent_contract_registry.md#5-registry-lookup-pattern)),
  composes LangGraph runs, and injects a signed **governance token**
  into every agent invocation.
- **Governance Authority** — the root of trust. Holds the ECDSA P-384 signing key,
  mints and revokes governance tokens, and enforces circuit-breaker / kill-switch
  state (see [agent_governance.md](../03_agents/agent_governance.md)).
- **Decision Engine** — consumes events, applies policy, and emits
  `DecisionEvent`s (see
  [Decision Engine flow](./event_driven_architecture.md#7-the-decision-engine)).

The Application Boundary is the **only** place that talks to all three internal
sub-boundaries (Agent, Data, Event). Agents do not.

### 4.3 Agent Boundary — interface `IF-AGENT`

**Perimeter:** the LangGraph runtime where agent reasoning executes.

This boundary is deliberately **sandboxed and outbound-restricted**:
- An agent receives, on `IF-AGENT`: its resolved contract, a signed governance
  token, its scoped input payload, and a tool proxy handle.
- An agent may **only**: reason, call tools through the tool proxy (`IF-TOOL`),
  read memory it is granted, and emit events. It has **no** direct network
  egress, **no** database driver, **no** cloud credentials, and **no** ability
  to reach the Integration Boundary directly.
- Every tool call and memory access is checked against the agent's contract
  (`allowed_tools`, `memory_read_access`, `memory_write_access`) and against the
  scope encoded in its governance token.

**How the agent layer interfaces with the infrastructure layer:** never
directly. Agents express *intent*; the infrastructure layer (tool proxy,
Decision Engine, integration adapters) performs *action*. An agent that wants to
publish a creative asset emits a `CreativeEvent`; it does not call S3. An agent
that wants to launch an ad emits intent consumed by the Decision Engine, which —
after governance checks — instructs the integration adapter.

**Rejection behavior:** A tool/memory call outside contract scope is denied at
the tool proxy, an `AuditEvent` + `GovernanceEvent`
(`type=governance.scope_violation`) is emitted, and the agent's `failure_mode`
(from its contract) is invoked. Repeat violations trip the **circuit breaker**
and suspend the agent
(see [circuit breaker rules](../03_agents/agent_governance.md#6-circuit-breaker-rules)).

### 4.4 Data Boundary — interface `IF-DATA`

**Perimeter:** Postgres (system of record), Qdrant (vector memory), Redis
(cache/queue), S3 (object storage).

**Contract (`IF-DATA`):**
- All access is via the **Data Access Layer (DAL)** repositories. No agent and
  no service holds raw DB credentials; the DAL does.
- **Tenant isolation is mandatory:** every row carries `org_id`; every query is
  filtered by the `RequestContext.org_id` via row-level security in Postgres and
  namespace partitioning in Qdrant (`tenant:{org_id}`) and S3
  (`s3://skylize/{org_id}/...`).
- Memory access by agents is additionally gated by the contract fields
  `memory_read_access` / `memory_write_access`, expressed as memory namespaces.
- Audit logs are append-only; the DAL exposes no update/delete path for them.

**Rejection behavior:** A cross-tenant or out-of-scope access attempt is denied
by RLS / namespace check, returns no data, and emits an `AuditEvent`
(`type=audit.access_denied`). This is treated as a security signal and forwarded
to the `fraud_detection_agent` pipeline.

### 4.5 Event Boundary — interface `IF-EVENT`

**Perimeter:** Redis Streams event bus (+ cold archive).
**Owns:** the canonical, ordered, replayable record of everything that happens.

This is the **only sanctioned asynchronous channel** between internal
components. Agents publish outputs here; the Decision Engine consumes and emits
here; the audit subsystem mirrors here. Schema, taxonomy, ordering, DLQ, replay,
and retention are fully specified in
[event_driven_architecture.md](./event_driven_architecture.md).

**Rejection behavior:** An event that fails schema validation (Pydantic v2,
versioned) is rejected at publish time, routed to the **Dead Letter Queue**, and
an `AuditEvent` (`type=audit.schema_rejected`) is recorded. It is never silently
dropped.

### 4.6 Integration Boundary — interfaces `IF-TOOL`, `IF-INTEGRATION`

**Perimeter:** every external system — n8n, Shopify, Stripe, Meta Ads, TikTok,
and the LLM providers.

This is the **only** boundary with outbound network egress, and it is reached
**only** by the infrastructure layer's **Integration Adapters**, never by agents.

> **Known tracked exception — ADR-0003.** The `website/` BFF console currently ships
> an *ungoverned* outbound n8n admin path (`api/console/workflows/route.ts` —
> create/activate/delete), reachable by a `skylize_console` session with **no**
> `GovernanceToken` / Decision-Engine check, which violates this rule. It is gated
> **default-off** (`SKYLIZE_ENABLE_N8N_ADMIN`, returns HTTP 501 unless explicitly
> enabled) as an interim band-aid; a governed rewrite through the Decision Engine/OPA
> is a **hard gate** before any production enablement. See
> [ADR-0003](../architecture/adr/0003-n8n-admin-governance-gap.md).

**Two named interfaces:**
- `IF-TOOL` — the inward face. An agent calls a tool via the tool proxy. The
  proxy validates contract + governance scope, then dispatches to an adapter.
- `IF-INTEGRATION` — the outward face. Each adapter owns credentials (in the
  secrets manager), translates internal intent to the external API, normalizes
  responses, and emits result events.

**How external integrations connect without crossing internal boundaries:**
- External systems **push** to Skylize only through the Edge Boundary via
  **signed webhooks** (Shopify HMAC, Stripe signature, Meta/TikTok app secret,
  n8n shared-secret HMAC). The webhook receiver verifies the signature, maps the
  payload to a typed internal event, and publishes to `IF-EVENT`. The external
  system never touches the Data or Application boundary directly.
- Skylize **pulls/writes** only through adapters at `IF-INTEGRATION`. n8n is
  treated as an external execution surface: Skylize triggers n8n workflows with a
  signed payload and receives signed callbacks; n8n holds **no** Skylize
  credentials and **cannot** read internal data — it only receives the scoped
  payload it is sent.

**Rejection behavior:** Inbound — a webhook with an invalid signature is dropped
at the edge with `401` and a `GovernanceEvent`
(`type=governance.integration_bad_signature`). Outbound — an adapter call that
violates the governance token scope, exceeds the agent's `max_token_budget`
(LLM adapters), or targets a disallowed external account is refused before
egress, and the originating agent's `escalation_path` is invoked
(see [agent_governance.md](../03_agents/agent_governance.md#3-authority--escalation)).

---

## 5. Boundary Enforcement Mechanisms

Enforcement is layered; each boundary has an independent control so a bypass at
one layer is still caught at the next.

### 5.1 API Gateway rules (Edge)
- Mandatory IdP JWT verification; no anonymous internal routes.
- Per-tenant and per-route rate limits.
- Strict request schema validation (Pydantic v2) before forwarding.
- Webhook signature verification for every external push.

### 5.2 Auth tokens (Edge → Application)
- IdP-issued JWT establishes **who** the human/tenant is.
- The Gateway derives a short-lived internal `RequestContext` (signed,
  ≤5 min TTL) carrying `org_id`, `user_id`, roles. Internal services trust the
  `RequestContext`, never the raw IdP token.

### 5.3 Signed governance tokens per agent (Application → Agent → Integration)

The **governance token** is the unit of agent authority and is defined
identically here and in
[agent_governance.md §4](../03_agents/agent_governance.md#4-governance-token) and
[agent_contract_registry.md §3](../03_agents/agent_contract_registry.md#3-governance-token).

Summary of its role at the boundary:
- Minted **only** by the Governance Authority (Application Boundary) using the
  root **ECDSA P-384** signing key ([ADR 0001](../architecture/adr/0001-governance-signature-scheme.md)).
- Injected into every agent invocation across `IF-AGENT`.
- Encodes: `token_id`, `agent_id`, `authority_level`
  (`executive`/`vp`/`director`/`manager`/`worker`), `department`,
  `delegation_chain`, `scope` (allowed tools/actions), `max_token_budget`,
  `max_execution_time_seconds`, `issued_at`, `expires_at`, `nonce`, `signature`.
- Validated by the **tool proxy** and every **integration adapter** before any
  side-effecting action: signature check → expiry check → revocation check →
  scope check. Any failure aborts the action.

A governance token is the *only* thing that lets agent intent become real-world
action. No token, no side effect.

### 5.4 Data Access Layer (Application → Data)
- Sole holder of DB/storage credentials.
- Enforces tenant isolation (RLS + namespacing) and contract-scoped memory
  access on every call.

---

## 6. Cross-Boundary Request Lifecycle (worked example)

A request to "generate three ad hooks for the spring campaign":

1. **Edge (`IF-EDGE`):** Client calls the Gateway with an IdP JWT. Verified →
   `RequestContext{org_id, user_id}`. (Fail → `401/403`, stop.)
2. **Application:** Orchestrator resolves the `hook_generator_agent` contract
   from the registry, checks circuit-breaker/kill-switch state, and mints a
   governance token scoped to that worker.
3. **Agent (`IF-AGENT`):** Agent runs in the sandbox with token + scoped input.
   It needs an LLM call.
4. **Tool (`IF-TOOL`):** Tool proxy validates token signature, expiry, and that
   `llm.generate` is in `allowed_tools` and `scope`, and that the call fits
   `max_token_budget`. (Fail → deny, `AuditEvent`, `failure_mode`.)
5. **Integration (`IF-INTEGRATION`):** LLM adapter (provider-abstracted) calls
   the chosen provider with credentials it alone holds.
6. **Event (`IF-EVENT`):** Agent emits a `CreativeEvent`
   (`type=creative.hooks_generated`). The Decision Engine consumes it and emits a
   `DecisionEvent`. An `AuditEvent` is recorded for every hop.
7. **Data (`IF-DATA`):** Decision Engine persists the approved hooks via the DAL,
   tenant-scoped to `org_id`.

Every hop is a named interface; every hop can reject; every rejection is audited.

---

## 7. Invariants (must always hold)

1. Agents never hold credentials, never have network egress, never touch the
   Data or Integration boundary directly.
2. External systems remain the system of record for the data they own; Skylize
   holds scoped, expiring mirrors and reference IDs only.
3. Every side-effecting action requires a valid, unexpired, unrevoked governance
   token whose scope permits it.
4. Every boundary crossing is observable: emitted as a typed event and recorded
   as an `AuditEvent`.
5. Tenant isolation is enforced at the Data Boundary regardless of any upstream
   check.
6. The event bus (`IF-EVENT`) is the only sanctioned internal async channel;
   there are no back-channels between components.
