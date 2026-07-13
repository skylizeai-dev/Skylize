# System Architecture (Index)

**Status:** Architecture index (points to the consolidated source of truth)
**Owner:** Principal Architect
**Related:** [../architecture/02_system_architecture.md](../architecture/02_system_architecture.md) · [system_boundaries.md](./system_boundaries.md) · [event_driven_architecture.md](./event_driven_architecture.md) · [service_map.md](./service_map.md)

---

## 1. Purpose

This file is the **entry point** to Skylize's system architecture from within the
`02_architecture/` spine folder. The full, consolidated production architecture —
layered view, the six interfaces, the three agent-communication modes, the
LangGraph orchestration model, and the end-to-end request lifecycle —
is maintained as a single source of truth in
[../architecture/02_system_architecture.md](../architecture/02_system_architecture.md).
This index summarizes it and routes the reader; it never restates it differently.

## 2. Architectural role

The system architecture sits **on top of** the two spine documents in this folder:
- [system_boundaries.md](./system_boundaries.md) defines *what* the boundaries are.
- [event_driven_architecture.md](./event_driven_architecture.md) defines *how*
  components talk asynchronously.
- The consolidated architecture defines *how the chosen components compose* into a
  running system across those boundaries.

## 3. The system in one screen

```
CLIENT (Next.js)
   │ HTTPS + OIDC JWT
EDGE  (Cloudflare → FastAPI gateway)            IF-EDGE
   │ derives signed RequestContext{org_id,user}
APPLICATION (service layer · Orchestrator ·     IF-AGENT / IF-DATA / IF-EVENT
             Decision Engine · Governance Authority)
   ├── AGENT RUNTIME  (LangGraph, sandboxed)            IF-TOOL
   ├── EVENT BUS      (Redis Streams + S3 archive)
   └── DATA ACCESS    (Postgres · Qdrant · Redis · S3, RLS by org_id)
INTEGRATION (adapters: LLM gateway, Shopify,    IF-INTEGRATION
             Stripe, Meta, TikTok, n8n — sole egress + credentials)
```

Five boundaries, six named interfaces. Full diagram and narrative:
[../architecture/02_system_architecture.md §2–3](../architecture/02_system_architecture.md#2-layered-view).

## 4. The three agent-communication modes (summary)

Agents never call each other directly; all communication is mediated.

| Mode | Direction | Mechanism | Detail |
|---|---|---|---|
| **A — Delegation** | down the hierarchy | `orchestrator.delegate`, inside one LangGraph run | [../architecture/02 §4.1](../architecture/02_system_architecture.md#41-mode-a--delegation-down-the-hierarchy-synchronous-within-a-graph) |
| **B — Event publication** | across departments | typed output wrapped as an event on `IF-EVENT` | [../architecture/02 §4.2](../architecture/02_system_architecture.md#42-mode-b--event-publication-across-departments-asynchronous) |
| **C — Decision request** | intent → outcome | proposal event consumed by the Decision Engine | [../architecture/02 §4.3](../architecture/02_system_architecture.md#43-mode-c--decision-request-intent--authorized-outcome) |

## 5. Orchestration (summary)

- **LangGraph** = the sole orchestration layer / durable control plane
  (checkpointed to Postgres; pauses at human-in-the-loop nodes; replayable).
  Intra-team collaboration runs as LangGraph subgraphs *inside* a node.
- **Orchestrator** = the facade that resolves the `AgentContract`, mints the
  `GovernanceToken`, wraps output as events, and audits every step.

LangGraph sits behind the Orchestrator facade and the contract registry, so the
orchestration layer is replaceable without touching agent contracts. Full division of labor:
[../architecture/02 §5](../architecture/02_system_architecture.md#5-orchestration-architecture)
and runtime detail in [../architecture/03_agent_runtime.md](../architecture/03_agent_runtime.md).

## 6. Multi-tenancy (summary)

`org_id` enters at the edge and is carried in `RequestContext`, every event
envelope, every governance token, and every DAL query. Isolation is enforced at
`IF-DATA` (Postgres RLS, Qdrant payload filter `tenant:{org_id}`, S3 prefix
`s3://skylize/{org_id}/...`) **independent of upstream checks**. Detail:
[../architecture/02 §7](../architecture/02_system_architecture.md#7-multi-tenancy)
and [../architecture/05_security_architecture.md §8](../architecture/05_security_architecture.md#8-tenant-isolation-defense-in-depth).

## 7. Ownership & evolution

- **Owner:** Principal Architect; the consolidated doc is the authoritative
  artifact this index tracks.
- **Evolution:** the same logical architecture runs at MVP (Docker Compose) and
  Scale (Kubernetes); each Scale move is a substitution behind a port already
  present in MVP ([../architecture/01_final_stack.md §5](../architecture/01_final_stack.md#5-mvp-stack-vs-scale-stack)).
  When the consolidated architecture changes, this index is updated in the same PR.
