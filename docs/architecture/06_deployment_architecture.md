# 06 — Deployment Architecture

**Status:** Production architecture (source of truth)
**Owner:** Principal Architect
**Related:** [01_final_stack.md](./01_final_stack.md) · [02_system_architecture.md](./02_system_architecture.md) · [05_security_architecture.md](./05_security_architecture.md) · existing [deployment_strategy.md](../09_development/deployment_strategy.md)

---

## 1. Purpose

How Skylize is packaged, deployed, scaled, and operated — from a single-host MVP
to a multi-tenant Kubernetes platform — with concrete migration triggers. The
guiding rule: **the same logical architecture at every tier; only the substrate
changes, behind ports already present in MVP** (see
[01_final_stack.md §5](./01_final_stack.md#5-mvp-stack-vs-scale-stack)).

---

## 2. Deployable units

All services are **12-factor containers** (Docker). One image set, configured by
environment per tier.

| Unit | Contains | Scales by |
|---|---|---|
| `gateway` | FastAPI edge: authN, rate limit, webhook receivers | replicas behind LB |
| `api` | service layer / business endpoints | replicas |
| `orchestrator` | contract resolution, token minting, LangGraph control plane | replicas (stateless between checkpoints) |
| `agent-worker` | LangGraph/CrewAI execution sandbox + tool proxy | replicas; per-tenant/agent limits |
| `decision-engine` | event consumer; emits DecisionEvents | replicas (consumer group) |
| `governance` | Governance Authority (signing, revocation, kill switch) | HA, restricted |
| `memory` | Memory service (VectorStore + MemoryRepository ports) | replicas |
| `integration-adapters` | LLM gateway + Shopify/Stripe/Meta/TikTok/n8n adapters | replicas; sole egress |
| `worker-archiver` | Redis→S3 event archiver | singleton/leader |

**Backing services** (stateful): PostgreSQL, Redis, Qdrant, S3/MinIO, secrets
manager. These are run as managed services or self-hosted operators, never inside
app containers.

---

## 3. Environments

| Env | Purpose | Substrate |
|---|---|---|
| `local` | developer machine | Docker Compose, single Postgres/Redis/Qdrant/MinIO |
| `staging` | pre-prod, replayable from prod-shaped fixtures | same shape as prod, smaller |
| `production` | live multi-tenant | MVP (Compose/VM) → Scale (K8s) |

Promotion is image-tag based; config differs per env, code does not.

---

## 4. MVP deployment (Docker Compose)

```
┌──────────────── single VM (or 2 for warm standby) ───────────────┐
│  Cloudflare → gateway → api / orchestrator / agent-worker         │
│              decision-engine · governance · memory · adapters     │
│  backing: Postgres (managed + PITR) · Redis (AOF) ·               │
│           Qdrant (single) · S3 (AWS or MinIO) · secrets manager   │
└───────────────────────────────────────────────────────────────────┘
```

- **Why start here:** minimal operational surface; one `docker compose up`
  brings the whole platform; fastest path to a real multi-tenant product.
- **Tenancy already present:** shared Postgres with RLS by `org_id`; per-tenant
  streams and Qdrant namespaces. Multi-tenant from day one — no rework to scale.
- **Resilience:** managed Postgres with point-in-time recovery; Redis AOF for
  event durability; nightly S3 archive of audit/governance per the retention
  floor.
- **Limits:** single-node ceilings on throughput/HA — which define the migration
  triggers in §6.

---

## 5. Scale deployment (Kubernetes)

```
┌──────────────────────── Kubernetes (EKS/GKE) ────────────────────┐
│  Ingress (Cloudflare → ALB) → gateway (HPA)                       │
│  Deployments: api, orchestrator, agent-worker, decision-engine,   │
│               memory, integration-adapters  (all HPA)             │
│  StatefulSet/Operators or managed: Postgres HA (+replicas),       │
│     Redis Cluster, Qdrant cluster, Vault                          │
│  Namespaces per layer; NetworkPolicies deny-by-default            │
│  S3 (AWS) with lifecycle tiering                                  │
└───────────────────────────────────────────────────────────────────┘
```

- **Why K8s only at Scale:** adopt it when HA/autoscaling genuinely require it —
  not before (avoid premature operational complexity, rule 2).
- **Compose → Helm:** each Compose service maps to a Helm chart; no code change.
- **Network policy** mirrors the boundaries: only the Application layer reaches
  Data/Event/Agent; only `integration-adapters` reach the internet
  ([05_security_architecture.md §11](./05_security_architecture.md#11-supply-chain--platform-hardening)).
- **Governance/signing** isolated in a restricted namespace with tight RBAC and
  Vault-backed key custody.

---

## 6. Migration triggers (MVP → Scale)

Migrate a component **only** when its trigger fires; migrate that component alone.

| Component | Trigger | Move |
|---|---|---|
| Compute | need >1 node for HA or autoscaling | Compose → K8s (Helm) |
| Postgres | write contention / RPO-RTO targets | single → HA cluster + read replicas → shard by `org_id` |
| Redis event bus | sustained throughput / retention beyond Redis comfort | single → Redis Cluster; **Kafka for the hottest department only** |
| Qdrant | recall latency / index size | single → cluster, shard by tenant |
| LLM gateway | QPS / availability SLO | single → HA + provider failover + response cache |
| Secrets | compliance / key-custody requirements | managed → Vault HA |
| Enterprise tenant | contractual isolation | shared DB → dedicated DB for that tenant |

Each move is a **substitution behind an existing port** (`EventBus`,
`VectorStore`, `ObjectStore`, `LLMGateway`, DAL). Domain and agent logic never
change.

---

## 7. CI/CD pipeline

```
PR → lint + type-check + unit tests → build image (pinned, SBOM, signed)
   → contract tests (schemas, events, AgentContract registry loads)
   → deploy to staging → smoke + replay tests → manual gate
   → deploy to production (rolling)
```

- **GitHub Actions**, OIDC to cloud (no long-lived keys).
- **Contract gate:** the build fails if any `AgentContract` is invalid or any
  event schema breaks compatibility — enforcing the registry's fail-closed rule
  ([agent_contract_registry.md §5](../03_agents/agent_contract_registry.md#5-registry-lookup-pattern)).
- **Rolling deploys** with health checks; governance and DB-migration steps are
  ordered and reversible.

---

## 8. Data lifecycle & backup

- **Postgres:** PITR + scheduled snapshots; tested restores into staging.
- **Redis:** AOF for durability; the archiver continuously ships events to S3
  Parquet **before** trimming, so nothing is lost on stream rollover.
- **Qdrant:** snapshots; fully rebuildable from Postgres canonical text + event
  log (so it is a derived store, not a backup-critical one — see
  [04_memory_architecture.md §8](./04_memory_architecture.md#8-consistency-model)).
- **S3 audit/governance:** object-lock, 7-year floor, immutable
  ([event_driven_architecture.md §11](../02_architecture/event_driven_architecture.md#11-retention-policy-per-event-type)).

---

## 9. Observability & operations

- **OpenTelemetry** traces every request edge→decision by `correlation_id`;
  exporters are vendor-neutral (Tempo/Jaeger/Grafana/Datadog interchangeable).
- **Langfuse** tracks LLM cost/quality keyed by `governance_token_id`.
- **Structured logs** (envelope minus PII) per hop.
- **SLOs & alerts** on DLQ rate, circuit-breaker trips, decision latency, token
  mint failures, per-tenant error budgets.
- **Runbooks** tie to existing [incident_response.md](../08_operations/incident_response.md)
  and [monitoring.md](../08_operations/monitoring.md).

---

## 10. Disaster recovery

| Scenario | Recovery |
|---|---|
| App node loss | replicas/HPA reschedule; stateless between checkpoints |
| Postgres loss | PITR restore; LangGraph state and canonical memory recovered |
| Redis loss | AOF restore; gaps rebuilt from S3 event archive (replay) |
| Qdrant loss | rebuild index from Postgres + `memory.*` event replay |
| Region loss (Scale) | cross-region S3 + warm standby; restore from snapshots |
| Compromised agent/dept | circuit breaker / kill switch isolates; replay audits the blast radius |

The event log + immutable audit archive make the system **reconstructable**: most
DR is "replay the durable log," not "hope the backup is fresh."

---

## 11. Deployment invariants

1. One image set; tiers differ by config, not code.
2. Every Scale step is a substitution behind an MVP port — no re-architecture.
3. Multi-tenant isolation holds at every tier (RLS + namespacing).
4. Only `integration-adapters` egress to the internet; boundaries enforced by
   network policy.
5. Stateful stores are backed up/replayable; the event log is the recovery spine.
6. The CI contract gate prevents deploying an inconsistent agent/event schema.
