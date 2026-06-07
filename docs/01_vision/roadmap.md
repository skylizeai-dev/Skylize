# Roadmap (Strategic)

**Status:** Strategic roadmap (source of truth for sequencing)
**Owner:** `ceo` · `cpo` · human owner
**Related:** [vision.md](./vision.md) · [mission.md](./mission.md) · [../11_product/feature_roadmap.md](../11_product/feature_roadmap.md) · [../11_product/mvp_definition.md](../11_product/mvp_definition.md)

---

## 1. Purpose

The strategic roadmap sequences **what we build, in what order, and why** — at the
altitude of capabilities and phases. The detailed, feature-level plan lives in
[../11_product/feature_roadmap.md](../11_product/feature_roadmap.md); this document
sets the phases that feature work ladders into.

## 2. Architectural role

The roadmap is constrained by one rule: **the spine ships first and never
regresses.** Governance, audit, boundaries, and the event bus are not a phase —
they are the foundation every phase builds on
([../README.md §2](../README.md#2-the-spine-read-these-first)). New capability is
added as governed crews on the existing spine, never as ungoverned shortcuts.

## 3. Phases

### Phase 0 — Foundation (the spine) ✅ in place
Boundaries, event bus, governance (token, breaker, kill switch), agent contract
registry, the decision engine, memory architecture, security model. This is what
makes everything after it safe.

### Phase 1 — MVP: governed creative + growth team
A working multi-tenant product where the Creative org (CMO/VP-Creative crews)
produces assets and Growth proposes campaigns; humans approve external launches;
everything is audited and replayable. Defined in
[../11_product/mvp_definition.md](../11_product/mvp_definition.md).

### Phase 2 — Breadth: more departments
Add Sales, Customer Success, Procurement, Finance-ops, and Security crews on the
same spine — each a contracted, governed department, not a bespoke integration.

### Phase 3 — Depth: organizational memory compounds
Decision/outcome history and playbooks make the org measurably better at the
owner's business; scoring/retrieval improve via the governed, tenant-isolated
learning pipeline ([../05_memory/learning_pipeline.md](../05_memory/learning_pipeline.md)).

### Phase 4 — Scale & enterprise
MVP→Scale substrate migration behind existing ports (Compose→K8s, Postgres HA/
shard, Redis→Cluster/Kafka-per-dept), enterprise isolation (dedicated DB), SOC2
attestation, SSO/RBAC mapping ([../architecture/06_deployment_architecture.md §6](../architecture/06_deployment_architecture.md#6-migration-triggers-mvp--scale)).

### Phase 5 — Business Operating System
Owners run their company through Skylize as one governed, accountable organization
spanning operations end to end ([vision.md §5](./vision.md#5-the-long-horizon-picture)).

## 4. Sequencing principles

1. **Governance before autonomy** in every phase — capability never outruns
   control.
2. **Same logical architecture at every tier** — scale is substitution behind
   ports, not re-architecture.
3. **Breadth via contracts** — a new department is new contracts + crews, not a
   new platform.
4. **Trust gates** — isolation, audit, and kill-switch readiness gate each phase's
   GA.

## 5. What does not move

The spine invariants (governed side effects, immutable audit, tenant isolation,
human override, anti-lock-in) are present from Phase 0 and hold through every
phase. No roadmap pressure relaxes them.

## 6. Ownership & evolution

- **Owner:** `ceo` and `cpo` under the human owner.
- **Evolution:** phases are reviewed quarterly against outcomes; the
  feature-level roadmap ([../11_product/feature_roadmap.md](../11_product/feature_roadmap.md))
  is the living tactical layer beneath these durable phases.
