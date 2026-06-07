# Technology Stack (Index)

**Status:** Architecture index (points to the consolidated source of truth)
**Owner:** Principal Architect
**Related:** [../architecture/01_final_stack.md](../architecture/01_final_stack.md) · [system_boundaries.md](./system_boundaries.md) · [service_map.md](./service_map.md)

---

## 1. Purpose

This file is the **entry point** to Skylize's technology selection from within the
`02_architecture/` folder. The authoritative decision record — every category,
its rationale, rejected alternatives, and migration path — is maintained as a
single source of truth in
[../architecture/01_final_stack.md](../architecture/01_final_stack.md). This index
summarizes the chosen stack and the rules behind it; it never selects differently.

## 2. Architectural role

The stack is chosen to make the boundary and governance model *cheap to enforce*:
one database with row-level security gives tenant isolation; one Redis gives the
cache, queue, **and** event bus; a provider-abstracted LLM gateway gives vendor
independence and cost control. Technology is subordinate to the invariants in the
spine — never the other way around.

## 3. Decision principles (the filter)

Every choice passes five hard rules, in priority order
([../architecture/01_final_stack.md §1](../architecture/01_final_stack.md#1-decision-principles)):

1. **Boring & proven** over newest.
2. **Minimize operational complexity** — reuse one system for several jobs.
3. **Avoid vendor lock-in** — open protocols, provider-abstracted ports.
4. **Self-hostable** — every core component can run on our own infra.
5. **Multi-tenant SaaS-ready** — per-tenant isolation (`org_id`) without re-architecture.

A choice that wins on rule 1 but loses on rule 3 or 4 is rejected.

## 4. The stack at a glance

| Category | Chosen |
|---|---|
| Backend language | **Python 3.12** |
| API framework | **FastAPI + Pydantic v2** |
| Frontend | **Next.js + TypeScript + Tailwind + shadcn/ui** |
| System of record | **PostgreSQL 16** (JSONB, FTS, RLS) |
| Vector store | **Qdrant** (primary), **pgvector** (fallback) |
| Cache + event bus + queue | **Redis 7 (Streams)** |
| Object storage | **S3 API** (AWS S3 / MinIO) |
| Agent orchestration | **LangGraph** (control) + **CrewAI** (team patterns) |
| Workflow automation | **n8n** (external execution surface) |
| LLM access | **provider-abstracted gateway** (OpenAI / Anthropic / Gemini) |
| Auth / identity | **OIDC IdP** (Clerk / Auth0 / Keycloak) |
| Edge / CDN / WAF | **Cloudflare** |
| Containers / orchestration | **Docker** → **Docker Compose (MVP)** → **Kubernetes (Scale)** |
| Observability | **OpenTelemetry + Langfuse + structured logs** |
| Secrets / IaC / CI | **Vault/KMS · Terraform · GitHub Actions** |

Per-row rationale, alternatives, and migration paths:
[../architecture/01_final_stack.md §4](../architecture/01_final_stack.md#4-category-decisions-rationale--alternatives--migration).

## 5. How Temporal / LangGraph / OPA fit (reconciliation)

The platform description sometimes references Temporal, LangGraph, and OPA. They
are reconciled into the canonical stack as follows, without displacing it:

- **LangGraph** is the **durable execution / control plane** for agent workflows
  (checkpointed to Postgres, resumable, replayable). It is the canonical
  orchestration choice ([../architecture/03_agent_runtime.md](../architecture/03_agent_runtime.md)).
- **Temporal-style durable execution** is realized *by* LangGraph's
  Postgres-checkpointed state machine; Skylize does not run a separate Temporal
  cluster in v1 (rule 2: one system per job). If a future workload needs
  general-purpose durable workflows beyond agent control flow, Temporal is
  adoptable behind the Orchestrator facade with no agent-contract change.
- **OPA (Open Policy Agent)** is the **policy engine behind the Decision Engine
  and Governance Authority** — it evaluates the authority/guardrail policies in
  [../04_decision_engine/guardrails.md](../04_decision_engine/guardrails.md). It
  governs decisions; it does not replace the `GovernanceToken` chain of trust,
  which remains the cryptographic root of every side effect.

## 6. One tool per job (rejected overlaps)

Operational complexity comes from redundant systems, so overlaps are deliberately
collapsed (e.g. Redis Streams instead of Kafka+Celery for MVP; Qdrant instead of
Pinecone; Postgres JSONB instead of a document DB; one LLM gateway instead of
scattered SDKs). Full list:
[../architecture/01_final_stack.md §3](../architecture/01_final_stack.md#3-rejected-overlaps-one-tool-per-job).

## 7. Anti-lock-in guarantees (invariants)

1. Every external dependency sits behind an internal port/adapter; no agent or
   domain module imports a vendor SDK directly.
2. We consume only commodity APIs for managed services (Postgres wire, S3 API,
   OIDC, OTel).
3. Every core component has a self-hostable path.
4. The LLM provider is never named in business logic — only in gateway adapters.
5. Tenancy (`org_id`) is enforced at the data layer regardless of substrate.

## 8. Ownership & evolution

- **Owner:** Principal Architect; `cto` and `vp_engineering` accountable for
  upholding the anti-lock-in invariants.
- **Evolution:** the MVP→Scale path substitutes substrate behind existing ports
  (Compose→K8s, single Postgres→HA+shards, Redis→Cluster/Kafka-per-dept, Qdrant
  single→cluster) with **no domain or agent change**
  ([../architecture/01_final_stack.md §5](../architecture/01_final_stack.md#5-mvp-stack-vs-scale-stack)).
  Any stack change updates the consolidated record and this index in the same PR.
