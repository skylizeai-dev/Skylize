# Technical Due Diligence

**Status:** Investor / enterprise-review material (source of truth for technical claims)
**Owner:** `cto` · Principal Architect · `chief_security_officer`
**Related:** [yc_overview.md](./yc_overview.md) · [../architecture/](../architecture/) · [../02_architecture/](../02_architecture/) · [../03_agents/agent_governance.md](../03_agents/agent_governance.md)

---

## 1. Purpose

A single document a technical investor or enterprise security team can read to
verify Skylize's architecture, security posture, and operational maturity, with
**direct links to the source-of-truth docs** behind every claim. Nothing here is
asserted that isn't specified and enforceable elsewhere.

## 2. Architecture at a glance

- **AI-native Business OS**, multi-tenant SaaS, event-driven, zero-trust agent
  runtime, replayable by construction.
- **Five boundaries / six interfaces** mediate every crossing
  ([../02_architecture/system_boundaries.md](../02_architecture/system_boundaries.md)).
- **Boring, proven, self-hostable stack** (Python/FastAPI, Postgres, Redis,
  Qdrant, S3), rigorous governance layered on top
  ([../architecture/01_final_stack.md](../architecture/01_final_stack.md)).

## 3. Governance & safety (the core of the diligence)

| Claim | Evidence |
|---|---|
| No side effect without a signed, scoped, unexpired token | [agent_governance.md §4](../03_agents/agent_governance.md#4-governance-token) |
| Tokens minted only by the Governance Authority (ECDSA P-384) | [../architecture/05_security_architecture.md §5](../architecture/05_security_architecture.md#5-the-governance-token-chain-of-trust) |
| Agents are untrusted: no egress, no creds, no DB driver | [../architecture/03_agent_runtime.md §4](../architecture/03_agent_runtime.md#4-the-agent-sandbox-if-agent) |
| Automatic containment (circuit breaker) | [agent_governance.md §7](../03_agents/agent_governance.md#7-circuit-breaker-rules) |
| Human override (kill switch) overrides all authority | [kill_switch_protocol.md](../04_decision_engine/kill_switch_protocol.md) |
| Policy-as-code (OPA), default deny, versioned | [guardrails.md](../04_decision_engine/guardrails.md) |
| Deterministic, explainable decisions | [decision_engine.md §5](../04_decision_engine/decision_engine.md#5-determinism-and-explainability) |

## 4. Security posture

| Area | Posture | Evidence |
|---|---|---|
| Identity | OIDC; short-lived signed `RequestContext` | [../architecture/05_security_architecture.md §3](../architecture/05_security_architecture.md#3-identity--authentication) |
| Authorization | 3 layers, most-restrictive-wins | [../07_security/permissions.md](../07_security/permissions.md) |
| Tenant isolation | RLS + namespacing at `IF-DATA`, independent of upstream; 0-breach SLO | [../architecture/05_security_architecture.md §8](../architecture/05_security_architecture.md#8-tenant-isolation-defense-in-depth) |
| Secrets | adapters/DAL only; never agents; ECDSA P-384 key custody restricted | [../architecture/05_security_architecture.md §7](../architecture/05_security_architecture.md#7-secrets-management) |
| Audit | immutable, object-locked, 7-year floor, replayable | [../02_architecture/event_driven_architecture.md §10-11](../02_architecture/event_driven_architecture.md#10-event-replay-debugging--compliance) |
| Supply chain | pinned deps, SBOM, signed images, least-priv CI | [../architecture/05_security_architecture.md §11](../architecture/05_security_architecture.md#11-supply-chain--platform-hardening) |
| Threat model | enumerated with controls | [../architecture/05_security_architecture.md §2](../architecture/05_security_architecture.md#2-threat-model-what-we-defend-against) |

## 5. Scalability & operations

- **Same logical architecture at MVP and Scale**; each scale move is a
  substitution behind an existing port — no re-architecture
  ([../architecture/06_deployment_architecture.md §6](../architecture/06_deployment_architecture.md#6-migration-triggers-mvp--scale)).
- **Observable by construction**: OTel traces by `correlation_id`, Langfuse LLM
  cost by `governance_token_id`, replay as authoritative reconstruction
  ([../08_operations/observability.md](../08_operations/observability.md)).
- **DR is replay-based**: the immutable event log makes the system reconstructable
  ([../architecture/06_deployment_architecture.md §10](../architecture/06_deployment_architecture.md#10-disaster-recovery)).

## 6. Anti-lock-in (a common enterprise objection, pre-answered)

Every dependency sits behind a port/adapter; only commodity APIs are bought
(Postgres wire, S3 API, OIDC, OTel); every core component is self-hostable; the
LLM provider is never named in business logic
([../architecture/01_final_stack.md §6](../architecture/01_final_stack.md#6-anti-lock-in-guarantees-invariants)).

## 7. SOC2 / compliance readiness

The controls SOC2 asks for are architectural here, not aspirational: access
control ([../07_security/permissions.md](../07_security/permissions.md)),
immutable audit, change management (CI contract gate), incident response
([../08_operations/incident_response.md](../08_operations/incident_response.md)),
and monitoring with SLOs ([../08_operations/monitoring.md](../08_operations/monitoring.md)).
Formal attestation is a Phase-4 milestone ([../01_vision/roadmap.md §3](../01_vision/roadmap.md#3-phases)).

## 8. Known issues & honesty

- Some agent-doc paths carry naming/structure anomalies (e.g. `vc_procurement`,
  `creative_operations_departmant` spelling, duplicate role files) tracked openly
  in [../02_architecture/repository_structure.md §5](../02_architecture/repository_structure.md#5-the-docs03_agents-mirror)
  and the [generation manifest](../03_agents/_generation_manifest.csv). Paths are
  preserved; canonical `agent_id`s are used in-doc.
- Cross-tenant learning is **off** at MVP; enabled only with the consent/de-id
  machinery in place ([../05_memory/learning_pipeline.md](../05_memory/learning_pipeline.md)).
- A graph DB is deliberately deferred; Postgres relations until proven otherwise.
- The `/console/*` operator UI is fully styled but runs entirely on mock data
  today — it is not yet wired to the live backend endpoints. Tracked openly in
  [docs/audits/console_state_audit.md](../audits/console_state_audit.md).
- User-auth (`/api/v1/auth/*`) and the credential vault (`/api/v1/credentials/*`)
  are **not started**: the routers exist but depend on a persistence,
  composition-root, and config layer that isn't built yet, so they are not
  mounted. This also means the console's auth guard is fail-closed rather than
  functional. Scoped out pending an owner and attack-surface sign-off — see
  [docs/audits/epic_user_auth_buildout.md](../audits/epic_user_auth_buildout.md).

## 9. Ownership & evolution

- **Owner:** `cto`, Principal Architect, `chief_security_officer`.
- **Evolution:** this document is the diligence index; it is updated alongside the
  architecture it references so every claim stays verifiable.
