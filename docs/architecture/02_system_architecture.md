# 02 — System Architecture

**Status:** Production architecture (source of truth)
**Owner:** Principal Architect
**Related:** [01_final_stack.md](./01_final_stack.md) · [system_boundaries.md](../02_architecture/system_boundaries.md) · [event_driven_architecture.md](../02_architecture/event_driven_architecture.md)

---

## 1. Purpose

This document defines how the chosen components ([01_final_stack.md](./01_final_stack.md))
compose into a running system: the layers, the boundaries, the agent
communication architecture, and the orchestration architecture. It is the
consolidated, production form of the spine's
[system_boundaries.md](../02_architecture/system_boundaries.md) and
[event_driven_architecture.md](../02_architecture/event_driven_architecture.md),
and it does not contradict them.

---

## 2. Layered view

```
┌───────────────────────────────────────────────────────────────┐
│  CLIENT          Next.js / TS / Tailwind / shadcn (SSR + API)  │
└───────────────────────────┬───────────────────────────────────┘
                            │ HTTPS (OIDC JWT)
┌───────────────────────────▼───────────────────────────────────┐
│  EDGE            Cloudflare → FastAPI Gateway                   │
│                  authN (OIDC) · rate limit · WAF · webhook HMAC │
│                  → derives signed RequestContext{org_id,user}   │
└───────────────────────────┬───────────────────────────────────┘
                            │ IF-EDGE
┌───────────────────────────▼───────────────────────────────────┐
│  APPLICATION     Service layer · Orchestrator · Decision Engine │
│                  · Governance Authority (mints GovernanceToken) │
└───┬───────────────┬───────────────────┬───────────────────────┘
    │ IF-AGENT      │ IF-EVENT           │ IF-DATA
┌───▼─────────┐ ┌───▼──────────────┐ ┌──▼────────────────────────┐
│ AGENT RUNTIME│ │ EVENT BUS        │ │ DATA ACCESS LAYER (DAL)    │
│ LangGraph    │ │ Redis Streams    │ │ Postgres · Qdrant · Redis  │
│ (sole orch.) │ │ (+ S3 archive)   │ │ · S3   (RLS by org_id)     │
│ (sandboxed)  │ └──────────────────┘ └────────────────────────────┘
└───┬──────────┘
    │ IF-TOOL (tool proxy)
┌───▼───────────────────────────────────────────────────────────┐
│  INTEGRATION     Adapters (sole egress + credentials)          │
│  IF-INTEGRATION  LLM Gateway · Shopify · Stripe · Meta · TikTok │
│                  · n8n (external execution surface)            │
└───────────────────────────────────────────────────────────────┘
```

Five boundaries, six named interfaces — defined authoritatively in
[system_boundaries.md](../02_architecture/system_boundaries.md). This doc adds the
*how* of agent communication and orchestration on top of those boundaries.

---

## 3. System boundaries (consolidated)

| Interface | Between | Carries | Enforced by |
|---|---|---|---|
| `IF-EDGE` | client ↔ application | OIDC JWT → `RequestContext` | Gateway (authN, rate limit, WAF, webhook HMAC) |
| `IF-AGENT` | application ↔ agent runtime | contract + `GovernanceToken` + scoped input | sandbox + tool proxy |
| `IF-DATA` | application ↔ data | tenant-scoped queries | DAL (RLS + namespacing) |
| `IF-EVENT` | all internal components | versioned Pydantic events | `EventBus.publish()` validation |
| `IF-TOOL` | agent ↔ tool proxy | tool-call intent | proxy (contract ∩ token scope) |
| `IF-INTEGRATION` | adapters ↔ external | normalized API calls | adapters (sole credentials + egress) |

**Invariants** (from the spine, restated): agents hold no credentials and have no
network egress; external systems remain system-of-record for their data; every
side effect requires a valid `GovernanceToken`; every crossing is audited; tenant
isolation is enforced at `IF-DATA` regardless of upstream checks; the event bus
is the only sanctioned internal async channel.

---

## 4. Agent communication architecture

Agents **never** call each other directly. All inter-agent communication is
**indirect, typed, and mediated** by the Orchestrator and the event bus. There
are exactly three communication modes:

### 4.1 Mode A — Delegation (down the hierarchy, synchronous within a graph)
A higher-authority agent delegates a bounded task to a lower one via the
Orchestrator's `orchestrator.delegate` tool. The Orchestrator:
1. resolves the callee's `AgentContract` from the registry,
2. checks governance state (suspension/kill switch),
3. mints a run-scoped `GovernanceToken` whose `delegation_chain` extends the
   caller's, and
4. runs the callee inside the same LangGraph execution.

This realizes the authority hierarchy in
[agent_governance.md §2-3](../03_agents/agent_governance.md#2-authority-hierarchy).
A caller can only delegate authority it holds.

### 4.2 Mode B — Event publication (across departments, asynchronous)
An agent produces a typed output (its contract `output_schema`); the Orchestrator
wraps it into the correct event (Creative/Sales/Memory/Decision/Governance/Audit),
stamps provenance (`source_agent_id`, `authority_level`, `governance_token_id`,
`causation_id`, `correlation_id`), validates against the department
PublisherContract, and publishes to `IF-EVENT`. Other departments consume via
their SubscriberContracts. Agents never write to streams directly. Full schema,
taxonomy, ordering, DLQ, replay, and retention live in
[event_driven_architecture.md](../02_architecture/event_driven_architecture.md).

### 4.3 Mode C — Decision request (intent → authorized outcome)
When an agent's output is a *proposal* (spend, launch, publish), it surfaces as an
event the **Decision Engine** consumes. The Decision Engine checks the
originating `authority_level` against the action's required level and emits
exactly one terminal `DecisionEvent`: `decision.approved`, `decision.rejected`,
or `decision.deferred_to_human`. Conflicts between overlapping mandates are
resolved per
[agent_governance.md §11](../03_agents/agent_governance.md#11-conflict-resolution).

**Why indirect-only:** it keeps the agent sandbox closed (`IF-AGENT`), makes every
interaction auditable and replayable, prevents hidden back-channels, and lets us
scale or relocate any department without rewiring callers.

### 4.4 Correlation model
- `correlation_id` ties an entire workflow (one user request → many agents →
  one outcome) into a single trace.
- `causation_id` links each event to the one that caused it.
- `partition_key` (e.g. `campaign:{id}`) gives per-entity ordering.

A single OTel trace spans edge → orchestrator → agents → decision, keyed by
`correlation_id`.

---

## 5. Orchestration architecture

### 5.1 Responsibilities of the Orchestrator
The Orchestrator (Application Boundary) is the single entry to the agent layer:
1. **Resolve** `AgentContract` from the registry (fail closed on unknown).
2. **Gate** on governance state (suspended / circuit-broken / killed).
3. **Validate** input against the contract `input_schema`.
4. **Mint** a run-scoped `GovernanceToken` (scope ⊆ `allowed_tools`).
5. **Run** the agent graph in the sandbox with the token + tool proxy.
6. **Validate** output, wrap as an event with provenance, publish.
7. **Audit** every step.

This is exactly the registry lookup / resolution pattern in
[agent_contract_registry.md §5](../03_agents/agent_contract_registry.md#5-registry-lookup-pattern).

### 5.2 LangGraph as the control plane
- Each workflow is a **LangGraph state machine**: nodes are agent steps or
  governance checkpoints; edges encode escalation, conflict, and
  human-in-the-loop branches.
- State is **durable** (checkpointed to Postgres), so a graph can pause at a
  `decision.deferred_to_human` node and resume on human approval — and can be
  **replayed** for debugging/compliance.
- Governance checks (token validity, authority, kill switch) are explicit nodes,
  not hidden middleware, so they are inspectable and testable.

### 5.3 LangGraph subgraphs for intra-team collaboration
Within a department crew (e.g. the copy team: `copy_director` coordinating
`hook_generator_agent`, `ad_copy_agent`, …), role-based collaboration is
expressed as a **LangGraph subgraph** — not a separate framework. The crew runs
as nodes *inside* the parent LangGraph run, so the control-plane guarantees
(durability, governance checkpoints, replay) wrap it exactly like any other step.

### 5.4 Division of labor
| Concern | Owner |
|---|---|
| Durable control flow, checkpoints, resume, replay | **LangGraph** |
| Governance checkpoints, HITL pauses, escalation/conflict branches | **LangGraph nodes** |
| Role-based team collaboration within a department | **LangGraph subgraph** (inside a node) |
| Contract resolution, token minting, event wrapping, audit | **Orchestrator** (facade over the runtime) |

LangGraph sits behind the Orchestrator facade and the `AgentContract`
registry, so the orchestration layer can be replaced without changing agent
contracts (see [01_final_stack.md §4.7](./01_final_stack.md#47-orchestration--langgraph-sole-orchestration-layer)
and [ADR-0002](./adr/0002-crewai-removal-langgraph-only.md)).

---

## 6. Request lifecycle (end to end)

"Generate three spring-campaign hooks and propose a launch":
1. **Edge:** OIDC JWT verified → `RequestContext{org_id,user}`.
2. **Orchestrator:** resolves `hook_generator_agent`, gates governance, mints
   token, starts a LangGraph workflow (`correlation_id` assigned).
3. **Agent (IF-AGENT):** reasons; needs an LLM call.
4. **Tool proxy (IF-TOOL):** validates token (sig→expiry→revocation→scope→budget),
   dispatches to the LLM Gateway adapter (IF-INTEGRATION).
5. **Event (IF-EVENT):** emits `creative.hooks_generated`; the launch *proposal*
   emits a `sales.campaign_proposed`.
6. **Decision Engine:** evaluates authority; worker can't launch → emits
   `decision.deferred_to_human`; LangGraph pauses at the HITL node.
7. **Human approves:** graph resumes; adapter launches via Meta/TikTok adapter.
8. **Data (IF-DATA):** approved artifacts persisted tenant-scoped (RLS).
9. Every hop emitted an `AuditEvent`; the whole path is one OTel trace.

---

## 7. Multi-tenancy

- Tenant identity (`org_id`) enters at the edge and is carried in
  `RequestContext`, every event envelope, every governance token, and every DAL
  query.
- Isolation is enforced at `IF-DATA` (Postgres RLS, Qdrant namespace `tenant:{org_id}`,
  S3 prefix `s3://skylize/{org_id}/...`) — independent of upstream checks.
- Streams are partitioned per tenant (`evt:{tenant}:{department}`).
- Scale tier may move enterprise tenants to dedicated DBs without app changes
  (see [01_final_stack.md §5](./01_final_stack.md#5-mvp-stack-vs-scale-stack)).
