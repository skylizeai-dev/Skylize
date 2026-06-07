# Retrieval Strategy

**Status:** Subsystem specification (source of truth for recall)
**Owner:** `director_memory_systems` · `director_ml` · `chief_data_officer`
**Related:** [memory_taxonomy.md](./memory_taxonomy.md) · [knowledge_graph.md](./knowledge_graph.md) · [../architecture/04_memory_architecture.md §6](../architecture/04_memory_architecture.md#6-read-path-recall) · [../04_decision_engine/scoring_models.md](../04_decision_engine/scoring_models.md)

---

## 1. Purpose

Retrieval strategy defines **how an agent recalls the right memory**: the hybrid
search that fuses vector similarity, keyword search, and relational lookup;
re-ranking; and the scoping rules that keep recall tenant-isolated and
contract-bound. Good recall is what makes agents context-aware; bad recall is what
makes them hallucinate or leak.

## 2. Architectural role

Retrieval is the **read path** of the Memory service
([../architecture/04_memory_architecture.md §6](../architecture/04_memory_architecture.md#6-read-path-recall)).
An agent calls `memory.search` (a granted tool); the tool proxy validates the
`GovernanceToken` and that the requested namespace ∈ `memory_read_access`; the
Memory service runs hybrid retrieval scoped by `org_id`; it emits
`memory.recall_served` and returns results. A read outside scope is denied at the
proxy → `audit.access_denied`.

## 3. Hybrid retrieval

No single index wins for every query, so retrieval **fuses three**:

```
query + scope(org_id, namespaces, k)
        │
   ┌────┼───────────────┬───────────────────┐
   ▼    ▼               ▼                   ▼
Qdrant vector      Postgres FTS       knowledge-graph
similarity         (keyword/BM25)     relational lookup
(payload-filtered  (RLS by org_id)    (RLS by org_id)
 org_id+namespace)
   └────┴───────────────┴───────────────────┘
                  fuse + re-rank
                        │
                        ▼
            top-k results + provenance
```

| Signal | Store | Good for |
|---|---|---|
| Vector similarity | Qdrant | "things like this," fuzzy/semantic recall |
| Keyword / FTS | Postgres | exact terms, names, IDs, recent text |
| Relational | knowledge graph | "connected to this entity" |

## 4. Fusion & re-ranking

- **Fusion:** results from the three signals are merged by **reciprocal rank
  fusion (RRF)** — robust, no score-scale calibration needed across stores.
- **Re-ranking:** the fused set is re-ranked by recency, outcome quality (did the
  recalled item lead to a good result?), and namespace priority. Re-ranking is
  deterministic and versioned so recall feeding a decision stays replayable.
- **Confidence:** retrieval returns a confidence; low confidence on context for an
  irreversible action contributes to the `LOW_CONFIDENCE_IRREVERSIBLE` HITL signal
  ([../04_decision_engine/scoring_models.md §5](../04_decision_engine/scoring_models.md#5-score-structure-illustrative)).

## 5. Scoping rules (non-negotiable)

1. **Tenant scope:** every query is filtered by `org_id` at every store (Qdrant
   payload filter, Postgres RLS). Cross-tenant recall is impossible and audited.
2. **Namespace scope:** results are restricted to the agent's `memory_read_access`
   namespaces; a worker cannot recall memory outside its task scope.
3. **PII handling:** recall returns canonical text the agent is entitled to;
   audit records use PII-safe hashes ([../architecture/04_memory_architecture.md §7](../architecture/04_memory_architecture.md#7-write-path-commit)).

## 6. Caching

Hot recalls are cached in Redis keyed by (`org_id`, namespace, query-hash),
invalidated on `memory.invalidated`. Cache is best-effort and never authoritative;
a miss falls through to the hybrid path
([memory_taxonomy.md §6](./memory_taxonomy.md#6-consistency)).

## 7. Feeding the Decision Engine

Scoring features ([../04_decision_engine/scoring_models.md](../04_decision_engine/scoring_models.md))
are sourced via this retrieval path — episodic outcomes, semantic patterns, and
graph relationships — so a score is computed from the same scoped, auditable
recall an agent would get, keeping decisions explainable and reproducible.

## 8. Ownership & evolution

- **Owner:** `director_memory_systems` (retrieval implementation), `director_ml`
  (re-ranking models), `chief_data_officer` (governance).
- **Evolution:** MVP uses RRF + deterministic re-rank; at Scale, a learned
  re-ranker (governed pipeline, [learning_pipeline.md](./learning_pipeline.md))
  may improve ordering while preserving determinism-per-version and the scoping
  invariants. Vector store swaps (Qdrant↔pgvector) are transparent behind the
  `VectorStore` port.
