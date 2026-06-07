# Memory Taxonomy

**Status:** Subsystem specification (source of truth for memory tiers)
**Owner:** `chief_data_officer` · `director_memory_systems` · Principal Architect
**Related:** [../architecture/04_memory_architecture.md](../architecture/04_memory_architecture.md) · [organizational_memory.md](./organizational_memory.md) · [knowledge_graph.md](./knowledge_graph.md) · [retrieval_strategy.md](./retrieval_strategy.md) · [learning_pipeline.md](./learning_pipeline.md) · [../03_agents/agent_contract_registry.md §2](../03_agents/agent_contract_registry.md#2-the-contract-schema)

---

## 1. Purpose

This document defines the **kinds of memory** Skylize has, what each is for, how
long it lives, which store backs it, and how agents are scoped to it. It is the
conceptual layer above the store-backed
[../architecture/04_memory_architecture.md](../architecture/04_memory_architecture.md);
the two never disagree.

## 2. Architectural role

Memory is **scoped, never global**. Every read/write is gated by the agent's
`memory_read_access` / `memory_write_access` namespaces
([../03_agents/agent_contract_registry.md §2](../03_agents/agent_contract_registry.md#2-the-contract-schema))
and isolated by `org_id`. Agents **propose** writes (`memory.write_requested`);
the Memory service **persists**. The event log is the source of truth — all memory
state is rebuildable by replaying `memory.*` events
([../02_architecture/event_driven_architecture.md](../02_architecture/event_driven_architecture.md)).

## 3. The six tiers

| Tier | Lifetime | Store | What it holds |
|---|---|---|---|
| **Working** | per run | LangGraph state (Postgres checkpoint) + Redis | scratchpad within one workflow |
| **Episodic** | medium | Postgres (events/outcomes) + Qdrant (embeddings) | "what happened and how it went" |
| **Semantic** | long | Qdrant (vectors) + Postgres (canonical text) | facts, patterns, brand voice |
| **Procedural** | long | Postgres | learned playbooks, approved workflows, policies |
| **Organizational / knowledge graph** | long | Postgres relations (+ Qdrant for fuzzy) | entities & relationships across the company |
| **Audit** | 7y, immutable | Redis (hot) → S3 Parquet (object-lock) | compliance/replay; never mutated |

Detailed by [organizational_memory.md](./organizational_memory.md) (org tier) and
[knowledge_graph.md](./knowledge_graph.md) (graph tier).

## 4. Namespaces

Memory is addressed by **namespace** strings, scoped under `org_id`. Namespaces
form a hierarchy that contracts grant against:

```
{domain}:{area}:{subarea}        e.g.  creative:copy:hooks
                                        brand:voice
                                        sales:leads:enriched
                                        security:fraud:signals
                                        strategy:directives
                                        org:decisions
```

- A contract's `memory_read_access` / `memory_write_access` lists namespaces or
  wildcards (`creative:*`, `org:*`). The tool proxy denies any access outside the
  granted set → `audit.access_denied`.
- **Workers that produce but don't persist** have empty `memory_write_access`
  (e.g. `hook_generator_agent`) — they propose, the system persists.
- Executives read broadly (`org:*`, `strategy:*`); workers read narrowly (their
  task namespace + relevant reference like `brand:voice`).

## 5. Tier-by-tier detail

### Working memory
Lives for one workflow run. Held in LangGraph durable state (so a paused HITL run
resumes with its scratchpad intact) plus Redis for sub-ms access. Never the source
of truth; discarded after the run.

### Episodic memory
Records concrete events and their outcomes ("campaign 42 launched → ROAS 2.1").
Postgres stores the canonical record; Qdrant stores embeddings for
similarity recall ("find campaigns like this one"). Feeds scoring features
([../04_decision_engine/scoring_models.md](../04_decision_engine/scoring_models.md)).

### Semantic memory
Durable facts and patterns: brand voice, high-performing hook patterns, known
fraud signatures, product facts. Qdrant for vector recall, Postgres for canonical
text. Rebuildable from canonical text + event log.

### Procedural memory
Approved playbooks and workflows — "how we do X here." Postgres-stored, versioned;
only written via governed approval (a `director`+ approval event), never by a raw
worker write.

### Organizational memory / knowledge graph
Entities (campaigns, vendors, customers, assets, agents) and their relationships.
See [organizational_memory.md](./organizational_memory.md) and
[knowledge_graph.md](./knowledge_graph.md).

### Audit memory
The compliance spine: immutable, object-locked, 7-year floor. Never mutated, only
appended; the basis of replay and DR
([../02_architecture/event_driven_architecture.md §11](../02_architecture/event_driven_architecture.md#11-retention-policy-per-event-type)).

## 6. Consistency

- **Postgres = source of truth** (transactional).
- **Qdrant = derived index** (eventually consistent; rebuildable).
- **Redis = volatile cache** (best-effort; invalidated on `memory.invalidated`).

Writes commit to Postgres *before* the vector index, so the canonical store is the
arbiter ([../architecture/04_memory_architecture.md §8](../architecture/04_memory_architecture.md#8-consistency-model)).

## 7. Ownership & evolution

- **Owner:** `chief_data_officer` (governance), `director_memory_systems`
  (implementation), Principal Architect (the tier model).
- **Evolution:** tiers are stable; stores migrate behind the `VectorStore` /
  `MemoryRepository` ports at Scale ([../architecture/04_memory_architecture.md §10](../architecture/04_memory_architecture.md#10-scale-migration)).
  New namespaces are additive and grant-gated; the scoped-never-global invariant
  is permanent.
