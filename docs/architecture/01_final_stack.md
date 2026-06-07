# 01 — Final Stack

**Status:** Production architecture decision (source of truth for technology selection)
**Owner:** Principal Architect
**Related spine:** [system_boundaries.md](../02_architecture/system_boundaries.md) · [event_driven_architecture.md](../02_architecture/event_driven_architecture.md) · [agent_governance.md](../03_agents/agent_governance.md) · [agent_contract_registry.md](../03_agents/agent_contract_registry.md)
**Companion docs:** [02_system_architecture.md](./02_system_architecture.md) · [03_agent_runtime.md](./03_agent_runtime.md) · [04_memory_architecture.md](./04_memory_architecture.md) · [05_security_architecture.md](./05_security_architecture.md) · [06_deployment_architecture.md](./06_deployment_architecture.md)

---

## 1. Decision principles

Every choice below is filtered through five hard rules, in priority order:

1. **Boring & proven** — prefer technology with a long track record and a deep
   operational corpus over the newest option.
2. **Minimize operational complexity** — fewer moving parts; reuse one system
   for several jobs before adding a new one.
3. **Avoid vendor lock-in** — choose open protocols and provider-abstracted
   interfaces; a managed offering is acceptable only when its API is a commodity
   (Postgres wire, S3 API, OIDC).
4. **Self-hostable** — every core component must run on our own infrastructure
   if a vendor relationship ends.
5. **Multi-tenant SaaS-ready** — every component must support per-tenant
   isolation (`org_id`) without re-architecture.

A technology that wins on rule 1 but loses on rule 3 or 4 is rejected. We do not
adopt anything we cannot, in principle, run ourselves.

---

## 2. Final stack at a glance

| Category | **Chosen** | Self-host? | Locks us in? |
|---|---|---|---|
| Language (backend) | **Python 3.12** | yes | no |
| API framework | **FastAPI + Pydantic v2** | yes | no |
| Async runtime | **asyncio + uvicorn/gunicorn** | yes | no |
| Frontend | **Next.js + TypeScript + Tailwind + shadcn/ui** | yes | no |
| System of record | **PostgreSQL 16** | yes | no (commodity wire) |
| Vector store | **Qdrant** | yes | no |
| Cache + event bus + queue | **Redis 7 (Streams)** | yes | no |
| Object storage | **S3 API (AWS S3 / MinIO)** | yes (MinIO) | no (S3 API is commodity) |
| Agent orchestration | **LangGraph (control) + CrewAI (team patterns)** | yes | no |
| Workflow automation | **n8n** (external execution surface) | yes | no |
| LLM access | **Provider-abstracted gateway** (OpenAI / Anthropic / Gemini) | n/a | **no** — abstraction is the point |
| Auth / identity | **OIDC IdP (Clerk or Auth0)** | swappable | no (OIDC standard) |
| Edge / CDN / WAF | **Cloudflare** | swappable | low |
| Containerization | **Docker** | yes | no |
| Orchestration (runtime) | **Docker Compose (MVP) → Kubernetes (Scale)** | yes | no |
| Observability | **OpenTelemetry + Langfuse + structured logs** | yes | no (OTel is vendor-neutral) |
| Secrets | **Cloud KMS-backed secrets manager / Vault** | yes (Vault) | low |
| IaC | **Terraform** | yes | no |
| CI/CD | **GitHub Actions** | swappable | low |

Each row is justified in §4 with rationale, alternatives, and migration path.

---

## 3. Rejected overlaps (one tool per job)

Operational complexity comes from *redundant* systems. We deliberately collapse
overlapping categories:

| Overlap rejected | We use instead | Why |
|---|---|---|
| Kafka / RabbitMQ / NATS as a second broker | **Redis Streams** | Redis is already present for cache+queue; Streams give ordering, consumer groups, DLQ, and replay. One system, three jobs. Kafka is reconsidered only at the Scale trigger in §5. |
| Celery / Sidekiq task queue | **Redis Streams consumers** | A second queue abstraction is redundant when the event bus already delivers, ACKs, and retries. |
| Pinecone / Weaviate / pgvector (as primary) | **Qdrant** | Qdrant is self-hostable, fast, and filter-rich. Pinecone is managed-only (lock-in). pgvector is kept as a *fallback*, not the primary (see 04). |
| A separate graph DB (Neo4j) for knowledge graph | **Postgres relations + Qdrant** | The knowledge graph is materialized in Postgres + vectors; a dedicated graph DB is deferred until graph traversal is a proven bottleneck. |
| Elasticsearch for search/logs | **Postgres FTS + OTel/Loki** | Avoids running and tuning an ES cluster for MVP-scale needs. |
| MongoDB / Dynamo as document store | **Postgres JSONB** | One database engine; JSONB covers document needs with transactional guarantees. |
| Multiple LLM SDKs scattered in code | **One provider-abstracted gateway** | Direct SDK calls are forbidden; everything goes through the gateway (lock-in avoidance + cost/quota control). |
| Building a bespoke workflow engine | **n8n** | Low-code automations live in n8n as an *external* surface (see boundaries), not reinvented internally. |
| Cloud-proprietary queue/eventing (SQS/SNS, EventBridge) | **Redis Streams** | Keeps the event bus portable and self-hostable. |

**Rule:** if a candidate only duplicates a job an existing component already does
well, it is rejected by default.

---

## 4. Category decisions (rationale · alternatives · migration)

### 4.1 Backend language — Python 3.12
- **Rationale:** the AI/agent ecosystem (LangGraph, CrewAI, provider SDKs,
  Pydantic) is Python-first; matching it removes an entire class of glue. 3.12
  for performance + typing maturity.
- **Alternatives:** Go (great ops story, weak agent ecosystem); Node/TS
  (we already use it on the frontend, but the agent libs are Python).
- **Migration path:** performance-critical hot paths can be extracted to Go/Rust
  microservices behind the same event/HTTP contracts without touching agents.

### 4.2 API framework — FastAPI + Pydantic v2
- **Rationale:** async-native, schema-first; Pydantic v2 is already the contract
  language across the spine (event envelopes, `AgentContract`, `GovernanceToken`).
  One validation model end to end.
- **Alternatives:** Django (heavier, sync-first); Litestar (less proven).
- **Migration path:** routes are thin adapters over the service layer; the
  framework can be swapped without changing domain logic or Pydantic models.

### 4.3 System of record — PostgreSQL 16
- **Rationale:** the most boring, proven, transactional database; JSONB, FTS,
  row-level security (our tenant-isolation primitive), and pgvector all in one.
- **Alternatives:** MySQL (weaker JSON/RLS story); a NoSQL primary (loses
  transactions and joins we rely on).
- **Migration path:** wire protocol is a commodity — move between AWS RDS,
  Aurora, CloudNativePG (self-host), or Crunchy with no app changes. Tenanting
  via RLS already; can shard by `org_id` later.

### 4.4 Vector store — Qdrant (primary), pgvector (fallback)
- **Rationale:** self-hostable, payload filtering (needed for per-tenant +
  per-namespace memory scoping), strong recall/latency, simple ops.
- **Alternatives:** Pinecone (managed-only → lock-in); Weaviate (heavier);
  Milvus (operationally complex).
- **Migration path:** the memory layer talks to a `VectorStore` port (see 04);
  swapping Qdrant↔pgvector↔other is an adapter change. pgvector covers small
  tenants/dev without running Qdrant.

### 4.5 Event bus + cache + queue — Redis 7 (Streams)
- **Rationale:** one system for three jobs (cache, queue, event bus). Streams
  provide ordering, consumer groups (at-least-once + DLQ), `XAUTOCLAIM`, and
  replay — exactly the semantics in
  [event_driven_architecture.md](../02_architecture/event_driven_architecture.md).
- **Alternatives:** Kafka (more durable/scalable but heavy ops); NATS JetStream
  (capable but another system to learn).
- **Migration path:** producers/consumers sit behind an `EventBus` port; a
  per-department migration to Kafka/NATS at Scale is an adapter swap, no
  business-logic change (see §5 and 02).

### 4.6 Object storage — S3 API (AWS S3 / MinIO)
- **Rationale:** S3 API is the de-facto commodity. MinIO gives bit-compatible
  self-hosting; AWS S3 for production durability.
- **Migration path:** identical API → move AWS S3 ↔ MinIO ↔ R2 ↔ GCS (S3 mode)
  by config.

### 4.7 Orchestration — LangGraph (control plane) + CrewAI (team patterns)
- **Rationale:** LangGraph gives explicit, inspectable, resumable state machines
  — essential for governance checkpoints, human-in-the-loop pauses, and replay.
  CrewAI expresses role/team collaboration ergonomically for department crews.
  LangGraph owns *control & durability*; CrewAI owns *intra-team collaboration*.
- **Alternatives:** AutoGen (less deterministic control); bespoke engine (cost,
  risk); CrewAI-only (weaker durable control); LangGraph-only (more boilerplate
  for team patterns).
- **Migration path:** both run behind the **Orchestrator** facade and the
  `AgentContract` registry (see 03); a framework can be retired by reimplementing
  the facade for affected agents while contracts stay identical.

### 4.8 LLM access — provider-abstracted gateway
- **Rationale:** vendor independence is a first-class requirement. A single
  internal gateway routes to OpenAI/Anthropic/Gemini, enforces
  `max_token_budget`, captures cost in Langfuse, and is the only egress to model
  providers (see [system_boundaries.md](../02_architecture/system_boundaries.md)).
- **Migration path:** add/drop a provider by adding an adapter; agents never name
  a provider directly.

### 4.9 Auth — OIDC IdP (Clerk or Auth0)
- **Rationale:** identity is undifferentiated heavy lifting; buy it, but only via
  the **OIDC** standard so it is swappable. IdP issues JWTs; the gateway derives
  the internal `RequestContext`.
- **Migration path:** OIDC compliance means Clerk ↔ Auth0 ↔ Keycloak
  (self-host) ↔ Ory swap with config + JWKS change, no app rewrite.

### 4.10 Runtime orchestration — Docker Compose → Kubernetes
- **Rationale:** start with the simplest thing that runs the whole system on one
  host (Compose). Adopt Kubernetes only when scaling/HA forces it (§5).
- **Migration path:** all services are 12-factor containers; Compose files map to
  Helm charts; no code change to move.

### 4.11 Observability — OpenTelemetry + Langfuse + structured logs
- **Rationale:** OTel is vendor-neutral instrumentation (traces/metrics/logs);
  Langfuse adds LLM-specific cost/quality tied to `governance_token_id`. No
  proprietary agent baked into the code.
- **Migration path:** OTel exporters point at Tempo/Jaeger/Datadog/Grafana
  interchangeably.

### 4.12 Secrets, IaC, CI/CD — Vault/KMS · Terraform · GitHub Actions
- **Rationale:** boring, standard, portable. Terraform describes infra
  declaratively; secrets never live in code (adapters at the Integration Boundary
  hold credentials).
- **Migration path:** Terraform is multi-cloud; CI is portable YAML; Vault
  self-hosts if we leave a managed secrets service.

---

## 5. MVP stack vs. Scale stack

The **same logical architecture** runs at both tiers; only the *operational
substrate* changes. This is intentional: no re-architecture between tiers, only
substitution behind ports.

| Concern | **MVP stack** | **Scale stack** | Trigger to migrate |
|---|---|---|---|
| Compute | Docker Compose on 1–2 VMs | Kubernetes (managed: EKS/GKE) | >1 node needed for HA, or autoscaling required |
| Postgres | Single managed instance + PITR | HA cluster + read replicas, shard by `org_id` | Write contention or RPO/RTO targets |
| Event bus | Redis Streams (single, AOF) | Redis Cluster; **Kafka per high-volume dept** | Sustained throughput / retention beyond Redis comfort |
| Vector | Qdrant single node (or pgvector) | Qdrant cluster, sharded by tenant | Recall latency / index size |
| Object store | MinIO or AWS S3 | AWS S3 + lifecycle to Glacier | Durability/compliance tiering |
| LLM gateway | Single service, per-tenant quotas | HA gateway + provider failover + caching | QPS / availability SLO |
| Tenancy | Shared DB, RLS by `org_id` | Shared DB + optional dedicated DB for enterprise | Enterprise isolation contracts |
| Secrets | Managed secrets manager | Vault HA | Compliance / key custody requirements |

**Migration philosophy:** every Scale move is a *substitution behind a port*
already present in MVP — `EventBus`, `VectorStore`, `ObjectStore`, `LLMGateway`,
DAL. We never rewrite domain or agent logic to scale; we change adapters and
topology. See [06_deployment_architecture.md](./06_deployment_architecture.md)
for the staged plan.

---

## 6. Anti-lock-in guarantees (invariants)

1. Every external dependency sits behind an internal **port/adapter**; no agent
   or domain module imports a vendor SDK directly.
2. We only consume **commodity APIs** where we buy managed services (Postgres
   wire, S3 API, OIDC, OTel).
3. Every core component (Postgres, Redis, Qdrant, MinIO, Keycloak, Vault,
   n8n) has a **self-hostable** path.
4. The LLM provider is never named in business logic — only in gateway adapters.
5. Tenancy (`org_id`) is enforced at the data layer regardless of substrate, so
   tiers can change without weakening isolation.
