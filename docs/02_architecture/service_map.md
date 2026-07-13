# Service Map

**Status:** Architecture reference (source of truth for service responsibilities)
**Owner:** Principal Architect · VP Engineering
**Related:** [system_boundaries.md](./system_boundaries.md) · [system_architecture.md](./system_architecture.md) · [../architecture/06_deployment_architecture.md](../architecture/06_deployment_architecture.md) · [../03_agents/agent_contract_registry.md](../03_agents/agent_contract_registry.md)

---

## 1. Purpose

This document is the **catalogue of runtime services**: what each service is
responsible for, which interface it owns, what it consumes and produces, who owns
it, and how it scales. It is the operational complement to
[system_boundaries.md](./system_boundaries.md) (which defines the boundaries) and
[../architecture/06_deployment_architecture.md](../architecture/06_deployment_architecture.md)
(which defines how they are packaged and deployed).

## 2. Architectural role

The service map is the contract between architecture and operations. Every alert,
runbook, and on-call rotation references a service named here. Every service maps
to exactly one boundary so that a failure is isolable to one trust zone.

## 3. Services

| Service | Boundary / interface | Responsibility | Consumes | Produces |
|---|---|---|---|---|
| `gateway` | Edge (`IF-EDGE`) | OIDC JWT verify, rate limit, WAF, webhook HMAC; derive `RequestContext` | HTTP, webhooks | authenticated requests |
| `api` | Application | business endpoints over the service layer | `RequestContext` | service calls |
| `orchestrator` | Application (`IF-AGENT`) | resolve `AgentContract`, mint token, run LangGraph control plane, wrap output→event | contracts, requests | agent runs, events |
| `governance` | Application | Governance Authority: ECDSA P-384 signing, mint/revoke, circuit breaker, kill switch | governance state | `GovernanceToken`, `governance.*` |
| `decision-engine` | Application | evaluate proposals against authority + policy; resolve conflicts | `creative.*`, `sales.*`, `decision.conflict_detected`, `governance.*` | `decision.*` |
| `agent-worker` | Agent (`IF-AGENT`) | sandboxed LangGraph execution + tool proxy | token + scoped input | typed agent output |
| `memory` | Data path | Memory service over `VectorStore` + `MemoryRepository` ports | `memory.write_requested`, recall calls | `memory.*` |
| `integration-adapters` | Integration (`IF-INTEGRATION`) | sole egress + credentials: LLM gateway, Shopify, Stripe, Meta, TikTok, n8n | tool-call intent (via proxy) | normalized results, result events |
| `worker-archiver` | Event | ship hot Redis events to S3 Parquet before trimming | event streams | cold archive |

Backing stateful stores (Postgres, Redis, Qdrant, S3/MinIO, secrets manager) are
run as managed services or operators, never inside app containers
([../architecture/06_deployment_architecture.md §2](../architecture/06_deployment_architecture.md#2-deployable-units)).

## 4. The agent contract registry as a service concern

The `orchestrator` is the runtime home of the **agent contract registry**
([../03_agents/agent_contract_registry.md](../03_agents/agent_contract_registry.md)):

- Contracts are versioned records in Postgres (`agent_contracts`, keyed by
  `agent_id` + `version`), validated against `AgentContract` on load. **Invalid
  contracts fail startup** (fail-closed).
- A hot in-memory map `agent_id → AgentContract` is cached and invalidated on a
  contract version bump.
- The CI **contract gate** rejects any build whose `AgentContract`s don't load or
  whose event schemas break compatibility
  ([../architecture/06_deployment_architecture.md §7](../architecture/06_deployment_architecture.md#7-cicd-pipeline)).

This is why the ~154 agent documents in `docs/03_agents/` and the code-level
contracts in `src/skylize/contracts/` must stay in lockstep: the registry is the
single point where a documented role becomes an enforceable runtime contract.

## 5. Service dependency direction

```
gateway → api → orchestrator → {governance, runtime(agent-worker), memory}
                     │
                     ├── events (EventBus) ──→ decision-engine ──→ events
                     └── dal ──→ Postgres/Qdrant/Redis/S3
runtime(tool proxy) → integration-adapters → external systems
worker-archiver: events → S3
```

Only `integration-adapters` reaches the internet; only the Application layer
reaches Data/Event/Agent. Network policy enforces this deny-by-default
([../architecture/05_security_architecture.md §11](../architecture/05_security_architecture.md#11-supply-chain--platform-hardening)).

## 6. Scaling per service

Each service scales independently; backing stores migrate behind ports only when
their trigger fires ([../architecture/06_deployment_architecture.md §6](../architecture/06_deployment_architecture.md#6-migration-triggers-mvp--scale)).
Stateless-between-checkpoints services (`orchestrator`, `agent-worker`,
`decision-engine`) scale by replicas; `governance` runs HA-but-restricted;
`worker-archiver` is a singleton/leader.

## 7. Ownership & evolution

- **Owner:** Principal Architect (boundaries), VP Engineering (implementation),
  with `director_platform` and `director_agent_infrastructure` owning specific
  services (see their agent specs under `CTO/Engineering`).
- **Evolution:** new external systems add an adapter to `integration-adapters` (or
  a new adapter service at Scale); new agent capabilities add contracts to the
  registry — never a new internal back-channel. Kafka may back the hottest
  department's stream at Scale behind the `EventBus` port with no service-API
  change.
