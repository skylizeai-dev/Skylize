# Feature Roadmap

**Status:** Product roadmap (source of truth for feature sequencing)
**Owner:** `cpo` · `vp_product` · `director_product_strategy`
**Related:** [mvp_definition.md](./mvp_definition.md) · [requierements.md](./requierements.md) · [../01_vision/roadmap.md](../01_vision/roadmap.md)

---

## 1. Purpose

This is the **tactical, feature-level plan** beneath the strategic phases in
[../01_vision/roadmap.md](../01_vision/roadmap.md). It enumerates concrete
features, the phase they belong to, and the spine capability they depend on — so
nothing ships ahead of the governance it requires.

## 2. Architectural role

Every feature row names a **dependency** on a spine/architecture capability. The
rule: a feature cannot ship before its governing capability is in place. This
keeps "governance before autonomy" concrete rather than aspirational.

## 3. Feature table

### Phase 1 — MVP (governed creative + growth)
| Feature | Depends on |
|---|---|
| Tenant onboarding + OIDC + isolation | identity/auth, RLS/namespacing |
| Creative crews (copy/art/video/brand/ops) | agent runtime, contracts, event bus |
| Campaign & budget proposals | decision engine, capital allocation |
| HITL approval queue (modify/approve/reject) | decision flow HITL nodes |
| Budget ceilings & approval rules UI | capital allocation, permissions |
| Shopify/Meta/TikTok/Stripe connect (scoped) | integration adapters |
| Audit & shadow-replay viewer | audit stream, replay |
| Per-tenant dashboards (health/decisions/spend) | observability, monitoring |
| Kill switch control | kill-switch protocol |

### Phase 2 — Breadth (more departments)
| Feature | Depends on |
|---|---|
| Sales crew (lead enrichment, signals, proposals) | event taxonomy `sales.*`, decision engine |
| Customer Success crew (lifecycle, retention, support) | contracts + crews |
| Procurement crew (sourcing, vendor scoring, contracts) | scoring models, capital allocation |
| Finance-ops crew (budgeting, profitability, risk) | capital allocation, BI |
| Security crew active defense (prompt-injection/fraud/audit) | security agents, fail-closed |

### Phase 3 — Depth (memory compounds)
| Feature | Depends on |
|---|---|
| Organizational memory & playbook promotion | organizational memory, governed approval |
| Knowledge-graph relational insights | knowledge graph |
| Per-tenant learned scoring/re-ranking | scoring models, retrieval |
| Opt-in cross-tenant learning (anonymized) | learning pipeline (consent/de-id gates) |

### Phase 4 — Scale & enterprise
| Feature | Depends on |
|---|---|
| Kubernetes substrate, HPA | deployment migration triggers |
| Postgres HA/shard, Redis Cluster/Kafka-per-dept | ports already present |
| Dedicated-DB enterprise isolation | data boundary |
| SSO group→role mapping, SCIM | permissions/RBAC |
| SOC2 attestation | audit, security, supply-chain controls |

### Phase 5 — Business Operating System
End-to-end governed operations; owner runs the company through Skylize.

## 4. Prioritization rule

Within a phase, features are ordered by **(trust-unlock × owner value) ÷ cost**,
but **never** ahead of their governing capability. A feature that would create an
ungoverned side effect is rejected regardless of value.

## 5. Ownership & evolution

- **Owner:** `cpo` / `vp_product`; `director_product_strategy` maintains the
  dependency mapping; `director_experimentation` runs feature A/B where applicable.
- **Evolution:** the table is living and reviewed each cycle against outcomes and
  the strategic phases; the dependency-gating rule is permanent.
