# Learning Pipeline

**Status:** Subsystem specification (source of truth for cross-tenant learning)
**Owner:** `chief_data_officer` · `director_ml` · `director_privacy` · `chief_security_officer`
**Related:** [memory_taxonomy.md](./memory_taxonomy.md) · [organizational_memory.md](./organizational_memory.md) · [retrieval_strategy.md](./retrieval_strategy.md) · [../04_decision_engine/scoring_models.md](../04_decision_engine/scoring_models.md) · [../architecture/04_memory_architecture.md §9](../architecture/04_memory_architecture.md#9-tenant-isolation--learning)

---

## 1. Purpose

The learning pipeline is how Skylize **improves over time** — refining scoring
weights, re-rankers, and pattern libraries from outcomes — **without ever
violating tenant isolation**. It is the single governed path by which any
aggregate or cross-tenant pattern is learned. Its existence is what lets the
platform get smarter while remaining a trustworthy multi-tenant SaaS.

## 2. Architectural role

The pipeline is **separate and governed**, distinct from the per-tenant memory
read/write paths. Default behavior is **strict per-tenant memory**: an agent only
ever sees its own tenant's data. Any learning that spans tenants operates on
**de-identified, consented** data through this pipeline — never by reading another
tenant's namespace ([../architecture/04_memory_architecture.md §9](../architecture/04_memory_architecture.md#9-tenant-isolation--learning)).

```
per-tenant data (NEVER leaves tenant scope for direct use)
        │  opt-in + consent gate (director_privacy)
        ▼
de-identification / anonymization (PII stripped, k-anonymity thresholds)
        │  chief_security_officer review for re-identification risk
        ▼
aggregate training set (no tenant attributable)
        │  director_ml trains/updates models
        ▼
candidate model/playbook  ──governed approval──▶  registered version
        │                                          (CI gate, holdout eval)
        ▼
deployed to scoring / re-ranking / pattern library (explainable, versioned)
```

## 3. What it learns

| Target | Source | Output |
|---|---|---|
| Scoring weights/curves | episodic outcomes (anonymized) | updated `Score` model version |
| Retrieval re-ranker | recall→outcome pairs (anonymized) | updated re-rank model version |
| Pattern libraries | high-performing patterns (anonymized) | semantic-memory pattern updates |
| Playbook proposals | recurring successful sequences | *proposed* procedural memory (needs governed approval) |

## 4. Hard constraints (non-negotiable)

1. **Opt-in only.** A tenant's data enters the cross-tenant pipeline only with
   explicit consent; default is excluded.
2. **De-identified only.** PII is stripped; aggregates enforce k-anonymity
   thresholds; no output is attributable to a tenant.
3. **No raw cross-tenant reads.** The pipeline never reads one tenant's namespace
   on behalf of another — it consumes only the anonymized aggregate set.
4. **Explainable outputs.** A learned model must still emit the named, weighted
   `contributions` structure ([../04_decision_engine/scoring_models.md §5](../04_decision_engine/scoring_models.md#5-score-structure-illustrative));
   black-box models cannot drive spend/launch decisions.
5. **Governed promotion.** No learned artifact reaches production without
   registration, holdout evaluation, and the CI contract gate.

## 5. Per-tenant learning (always allowed)

Independent of the cross-tenant pipeline, **within a single tenant** the system
continuously materializes organizational memory and may tune that tenant's own
scoring within its own data — this needs no consent gate because it never leaves
tenant scope ([organizational_memory.md §6](./organizational_memory.md#6-tenant-isolation)).

## 6. Governance & audit

- Every pipeline run is audited: inputs (anonymized set id), model version
  produced, evaluation metrics, approver.
- `director_privacy` owns the consent + de-identification gate; `chief_security_officer`
  reviews re-identification risk; `chief_data_officer` owns model governance;
  `director_ml` owns training. A promotion is a `governance.*` config event.
- Privacy/compliance constraints map to the `brand_legal` and `data_access`
  guardrail policies ([../04_decision_engine/guardrails.md §3](../04_decision_engine/guardrails.md#3-policy-classes)).

## 7. Ownership & evolution

- **Owner:** `chief_data_officer` (overall), `director_ml` (training),
  `director_privacy` (consent/anonymization), `chief_security_officer`
  (re-identification risk).
- **Evolution:** MVP ships with **no cross-tenant learning enabled** — hand-set,
  explainable models per tenant. Cross-tenant learning is introduced only when the
  consent, de-identification, and review machinery above is in place and audited.
  The five hard constraints are permanent.
