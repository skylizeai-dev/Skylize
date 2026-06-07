# MVP Definition

**Status:** Product specification (source of truth for MVP scope)
**Owner:** `cpo` · `vp_product` · human owner
**Related:** [requierements.md](./requierements.md) · [user_personas.md](./user_personas.md) · [feature_roadmap.md](./feature_roadmap.md) · [../01_vision/roadmap.md](../01_vision/roadmap.md) · [../architecture/06_deployment_architecture.md §4](../architecture/06_deployment_architecture.md#4-mvp-deployment-docker-compose)

---

## 1. Purpose

This document defines **the smallest product that proves the thesis**: a governed
AI organization that produces real work and acts on it under human authority,
fully audited, multi-tenant. It draws the in/out line for Phase 1
([../01_vision/roadmap.md §3](../01_vision/roadmap.md#3-phases)).

## 2. The MVP thesis

A business owner connects their store and ad accounts, sets budget ceilings and
approval rules, and a **governed Creative + Growth team** produces creative,
proposes campaigns, and — with human approval on first/over-ceiling actions —
launches and optimizes, with every action explainable and reversible.

## 3. In scope

| Capability | Detail |
|---|---|
| **Multi-tenant onboarding** | OIDC auth, `org_id` isolation from day one (RLS + namespacing) |
| **Creative crew** | VP-Creative org: copy/art/video/brand/creative-ops teams produce assets ([../03_agents/...](../03_agents/00_organization_chart.md)) |
| **Growth proposals** | campaign + budget reallocation proposals via the Decision Engine |
| **Decision Engine** | authority + OPA policy + scoring + capital + HITL ([../04_decision_engine/decision_engine.md](../04_decision_engine/decision_engine.md)) |
| **Governance** | token minting, circuit breaker, kill switch ([../03_agents/agent_governance.md](../03_agents/agent_governance.md)) |
| **HITL approvals** | first external launch, over-ceiling spend, brand/legal-sensitive |
| **Integrations** | LLM gateway, Shopify (read mirror), Meta/TikTok (scoped), Stripe (billing refs) ([../06_integrations/](../06_integrations/)) |
| **Memory** | working/episodic/semantic + organizational memory of decisions/outcomes |
| **Audit & replay** | immutable audit stream; shadow replay for debugging |
| **Observability** | OTel traces, Langfuse cost, per-tenant dashboards |

## 4. Out of scope (deferred)

- Most non-creative departments (Sales/CS/Procurement/Finance-ops as full crews) → Phase 2.
- Cross-tenant learning pipeline (per-tenant only at MVP) → Phase 3.
- Kubernetes/Scale substrate (Docker Compose at MVP) → Phase 4.
- Dedicated-DB enterprise isolation, SOC2 attestation → Phase 4.
- A graph database (Postgres relations suffice) → only if proven necessary.

## 5. MVP non-negotiables (the spine, present at MVP)

Even at MVP, **all spine invariants hold**: governed side effects, immutable
audit, tenant isolation at `IF-DATA`, human override (kill switch), provider
abstraction. The MVP is small in *breadth*, never in *governance*.

## 6. Definition of done

- A tenant can onboard, set ceilings/approvals, and get produced creative +
  campaign proposals.
- No external launch or over-ceiling spend happens without human approval.
- Every action is auditable and shadow-replayable.
- Cross-tenant isolation verified (0 breaches in test).
- Kill switch and circuit breaker exercised in staging.
- Runs on Docker Compose; promotes by image tag through staging to production.

## 7. Success metrics

| Metric | Target (MVP) |
|---|---|
| Time-to-first-creative after onboarding | minutes |
| % actions correctly gated (no ungoverned side effects) | 100% |
| Cross-tenant isolation breaches | 0 |
| Decision latency (non-HITL) | p95 < 2s |
| Owner trust signal (approves continued autonomy) | qualitative + retention |

## 8. Ownership & evolution

- **Owner:** `cpo` / `vp_product` under the human owner.
- **Evolution:** MVP graduates to Phase 2 by adding governed departments as
  contracts+crews on the same spine, never by relaxing the non-negotiables.
