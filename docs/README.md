# Skylize Documentation

**Status:** Master index (source of truth for navigation)
**Owner:** Principal Architect
**Audience:** engineers, security reviewers, auditors, investors performing due diligence

---

## 1. What Skylize is

Skylize is an **AI-native Business Operating System**: a multi-tenant SaaS platform
that runs a company's go-to-market and operations as a governed hierarchy of
autonomous agents. It is built as an **event-driven**, **zero-trust**,
**replayable** system in which every agent action is contract-bound, every side
effect requires a signed governance token, and every state change is
reconstructable from an immutable event log.

The platform is deliberately **boring where it can be and rigorous where it must
be**: a small, proven infrastructure stack (Python, Postgres, Redis, Qdrant, S3)
carries a strict governance and audit model designed up front for YC due
diligence, enterprise security review, and SOC 2.

---

## 2. The spine (read these first)

Four foundation documents define the invariants every other file obeys. They are
the **source of truth**; if any document conflicts with them, the spine wins.

| Document | Defines |
|---|---|
| [system_boundaries.md](02_architecture/system_boundaries.md) | The five boundaries and six named interfaces (`IF-EDGE/AGENT/DATA/EVENT/TOOL/INTEGRATION`); what Skylize owns vs. external systems |
| [event_driven_architecture.md](02_architecture/event_driven_architecture.md) | The Redis Streams event bus, the versioned Pydantic event envelope, the six-category taxonomy, ordering, DLQ, replay, retention |
| [agent_governance.md](03_agents/agent_governance.md) | The authority ladder, escalation, the `GovernanceToken`, circuit breaker, kill switch, HITL, audit, conflict resolution |
| [agent_contract_registry.md](03_agents/agent_contract_registry.md) | The `AgentContract` Pydantic schema and the Orchestrator registry-lookup pattern |

The consolidated, store-backed production form of the spine lives in
[docs/architecture/](architecture/) (final stack, system, runtime, memory,
security, deployment).

---

## 3. Canonical vocabulary (used identically everywhere)

These terms have one meaning across all documents. Do not introduce synonyms.

- **Authority levels:** `executive` · `vp` · `director` · `manager` · `worker`
- **Interfaces:** `IF-EDGE` · `IF-AGENT` · `IF-DATA` · `IF-EVENT` · `IF-TOOL` · `IF-INTEGRATION`
- **Event categories:** `creative` · `sales` · `memory` · `decision` · `governance` · `audit`
- **Failure modes:** `retry_then_escalate` · `escalate_immediately` · `fail_closed` · `fallback_degraded`
- **Token validation order:** signature → expiry → revocation → scope → budget → delegation
- **Core objects:** `BaseEvent` · `AgentContract` · `GovernanceToken` · `ToolGrant` · `RequestContext`
- **Tenant key:** `org_id` (carried in `RequestContext`, every event, every token, every DAL call)

---

## 4. Documentation map

| Section | Purpose |
|---|---|
| [01_vision/](01_vision/) | Mission, vision, roadmap — why Skylize exists and where it is going |
| [02_architecture/](02_architecture/) | The spine boundary/event docs + system, repository, service, and stack docs |
| [architecture/](architecture/) | Consolidated production architecture (stack, system, runtime, memory, security, deployment) |
| [03_agents/](03_agents/) | Governance, the contract registry, the org chart, and ~154 agent role specifications |
| [04_decision_engine/](04_decision_engine/) | Decision flow, scoring, OPA guardrails, capital allocation, kill-switch protocol |
| [05_memory/](05_memory/) | Memory taxonomy, organizational memory, knowledge graph, retrieval, learning pipeline |
| [06_integrations/](06_integrations/) | Adapter specs for each external system (LLM providers, Shopify, Stripe, Meta, TikTok, n8n, MCP) |
| [07_security/](07_security/) | Permissions / authorization model (RBAC × contract × token) |
| [08_operations/](08_operations/) | Incident response, monitoring, observability |
| [09_development/](09_development/) | Coding standards, deployment strategy |
| [10_investor_materials/](10_investor_materials/) | YC overview, technical due diligence |
| [11_product/](11_product/) | MVP definition, requirements, personas, feature roadmap |

---

## 5. How to read this as a new engineer

1. This file, then the four spine documents (§2).
2. [architecture/02_system_architecture.md](architecture/02_system_architecture.md) for the end-to-end request lifecycle.
3. [architecture/03_agent_runtime.md](architecture/03_agent_runtime.md) for how an agent actually executes.
4. [03_agents/00_organization_chart.md](03_agents/00_organization_chart.md) for the org and a worked agent example.
5. The section relevant to your team.

Every document states its **purpose**, **architectural role**, **interactions**,
**ownership**, and **evolution path**, so each is understandable without external
explanation.

---

## 6. Build provenance

The order in which files were authored — the consistency anchor for the whole
build — is recorded in [_BUILD_LOG.md](_BUILD_LOG.md). The agent hierarchy is
mechanically derived by `scripts/gen_manifest.js` into
[03_agents/_generation_manifest.csv](03_agents/_generation_manifest.csv), which is
the authoritative source for each agent's `authority_level`, parent, and
`escalation_path`.
