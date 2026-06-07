# Product Requirements

**Status:** Product specification (source of truth for requirements)
**Owner:** `cpo` · `vp_product` · `director_product_strategy`
**Related:** [mvp_definition.md](./mvp_definition.md) · [user_personas.md](./user_personas.md) · [feature_roadmap.md](./feature_roadmap.md) · [../07_security/permissions.md](../07_security/permissions.md)

> **Note:** the filename `requierements.md` retains the original (misspelled) path
> on disk to preserve links and history; the canonical title is *Product
> Requirements*.

---

## 1. Purpose

This document captures the **functional and non-functional requirements** the
platform must satisfy, traceable to the architecture that satisfies them. It is
the bridge between product intent ([mvp_definition.md](./mvp_definition.md)) and
the spine.

## 2. Functional requirements

| ID | Requirement | Satisfied by |
|---|---|---|
| FR-1 | Tenants authenticate via OIDC; each request carries verified `org_id` | [../architecture/05_security_architecture.md §3](../architecture/05_security_architecture.md#3-identity--authentication) |
| FR-2 | Agents produce typed outputs that become events | [../02_architecture/event_driven_architecture.md §6](../02_architecture/event_driven_architecture.md#6-publisher--subscriber-contracts-per-department) |
| FR-3 | No spend/launch without authority + policy + (if triggered) human approval | [../04_decision_engine/decision_engine.md](../04_decision_engine/decision_engine.md) |
| FR-4 | Owners set budget ceilings and approval rules per scope | [../04_decision_engine/capital_allocation.md](../04_decision_engine/capital_allocation.md) |
| FR-5 | Humans can approve/modify/reject any deferred decision | [../04_decision_engine/decision_flow.md §7](../04_decision_engine/decision_flow.md#7-hitl-pause--resume-stage-6-detail) |
| FR-6 | Owners can stop any agent/department/tenant immediately | [../04_decision_engine/kill_switch_protocol.md](../04_decision_engine/kill_switch_protocol.md) |
| FR-7 | Connect Shopify/Meta/TikTok/Stripe via scoped adapters | [../06_integrations/](../06_integrations/) |
| FR-8 | Every action is auditable and replayable | [../02_architecture/event_driven_architecture.md §10-11](../02_architecture/event_driven_architecture.md#10-event-replay-debugging--compliance) |
| FR-9 | Per-tenant dashboards for health, decisions, spend, audit | [../08_operations/monitoring.md §7](../08_operations/monitoring.md#7-dashboards) |
| FR-10 | Agents recall relevant, scoped memory | [../05_memory/retrieval_strategy.md](../05_memory/retrieval_strategy.md) |

## 3. Non-functional requirements

| ID | Requirement | Target / mechanism |
|---|---|---|
| NFR-1 **Isolation** | zero cross-tenant data access | RLS + namespacing at `IF-DATA`; 0 breaches (SEV1) |
| NFR-2 **Auditability** | reconstruct any action | immutable, object-locked audit; 7-year floor |
| NFR-3 **Availability** | gateway 99.9% | replicas, HPA at Scale |
| NFR-4 **Latency** | non-HITL decision p95 < 2s | [../08_operations/monitoring.md §5](../08_operations/monitoring.md#5-slos-illustrative-targets) |
| NFR-5 **Security** | zero-trust agents; signed tokens | [../architecture/05_security_architecture.md](../architecture/05_security_architecture.md) |
| NFR-6 **No lock-in** | self-hostable, port/adapter for every dependency | [../architecture/01_final_stack.md §6](../architecture/01_final_stack.md#6-anti-lock-in-guarantees-invariants) |
| NFR-7 **Cost control** | per-agent LLM budget ceilings | token `max_token_budget` + Langfuse |
| NFR-8 **Compliance-ready** | SOC2/enterprise-review oriented | audit, isolation, secrets, supply-chain controls |
| NFR-9 **Recoverability** | reconstruct from event log | replay-based DR ([../architecture/06_deployment_architecture.md §10](../architecture/06_deployment_architecture.md#10-disaster-recovery)) |

## 4. Constraints (inherited invariants)

- Agents hold no credentials and have no network egress.
- External systems remain system-of-record for their data; Skylize holds scoped
  mirrors and reference IDs.
- Every side effect requires a valid governance token.
- Tenant isolation is enforced at the data layer regardless of upstream checks.

## 5. Acceptance & traceability

Each requirement maps to: (a) the architecture doc that defines its mechanism,
(b) a contract test or replay test in CI where applicable
([../architecture/06_deployment_architecture.md §7](../architecture/06_deployment_architecture.md#7-cicd-pipeline)),
and (c) a monitored SLI ([../08_operations/monitoring.md](../08_operations/monitoring.md)).
A requirement without an enforcing mechanism is not "done."

## 6. Ownership & evolution

- **Owner:** `cpo` / `vp_product`; `director_product_strategy` maintains the
  traceability matrix; `director_user_research` validates against personas.
- **Evolution:** requirements grow by phase ([../01_vision/roadmap.md](../01_vision/roadmap.md));
  NFRs (isolation, audit, security) only tighten, never loosen.
