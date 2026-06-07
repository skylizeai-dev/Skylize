# Knowledge Graph

**Status:** Subsystem specification (source of truth for the entity graph)
**Owner:** `chief_data_officer` · `director_memory_systems` · `director_analytics`
**Related:** [memory_taxonomy.md](./memory_taxonomy.md) · [organizational_memory.md](./organizational_memory.md) · [retrieval_strategy.md](./retrieval_strategy.md) · [../architecture/04_memory_architecture.md §4.2](../architecture/04_memory_architecture.md#42-postgresql--canonical-text-relations-knowledge-graph-procedural)

---

## 1. Purpose

The knowledge graph is the **entities-and-relationships** view of a tenant's
business: how campaigns relate to creatives, vendors to contracts, customers to
orders, agents to decisions. It lets the platform answer relational questions
("which creatives drove the top customers?", "which vendor risks touch which
active campaigns?") that flat records and vectors alone cannot.

## 2. Architectural role

Per the stack decision, the knowledge graph is **materialized in Postgres
relations** (with Qdrant for fuzzy/semantic matching), **not** a separate graph
database. A dedicated graph DB (e.g. Neo4j) is deliberately deferred until graph
traversal is a *proven* bottleneck — one database engine, fewer moving parts
([../architecture/01_final_stack.md §3](../architecture/01_final_stack.md#3-rejected-overlaps-one-tool-per-job)).
Like all organizational memory, the graph is a projection of the event log and is
fully rebuildable.

## 3. Core entities & relationships

```
Customer ──places──▶ Order ──contains──▶ Product
   │                                         ▲
   └──segment──▶ Segment            ┌─sources─┘
                                    Vendor ──under──▶ Contract
Campaign ──uses──▶ Creative ──variant_of──▶ CreativeAsset
   │                  │
   └──targets──▶ Audience           Agent ──made──▶ Decision ──about──▶ {Campaign|Spend|Vendor}
```

| Entity | Source events | Namespace |
|---|---|---|
| Campaign, Creative, Asset | `creative.*`, `sales.campaign_*` | `creative:*`, `sales:*` |
| Customer, Order, Product | Shopify webhooks → `sales.*` | `sales:*` (mirror; Shopify is SoR) |
| Vendor, Contract | procurement events | `procurement:*` |
| Agent, Decision | `decision.*`, `governance.*` | `org:decisions` |

Note external entities (Customer/Order/Product) are **scoped mirrors** — Shopify
remains the system of record ([../02_architecture/system_boundaries.md §3.2](../02_architecture/system_boundaries.md#32-external-systems-own)).

## 4. Storage model

- **Nodes** = rows in typed Postgres tables, each carrying `org_id` (RLS).
- **Edges** = rows in relationship tables (`edge(src_id, src_type, rel, dst_id,
  dst_type, org_id, attrs jsonb)`), indexed for the common traversals.
- **Fuzzy match** = Qdrant embeddings on node text (names, descriptions) for
  "entities like this" lookups, payload-filtered by `org_id`.
- Traversal uses recursive CTEs in Postgres for bounded-depth queries; depth is
  capped to keep queries predictable.

## 5. How it is built & queried

- **Built** by the Memory service projecting entities/edges from `*.committed`
  and domain events — never by ad-hoc agent writes. The graph is eventually
  consistent with the event log and rebuildable from it.
- **Queried** via the Memory service's relational read path
  ([retrieval_strategy.md](./retrieval_strategy.md)), gated by the agent's
  `memory_read_access`. A worker can traverse only the subgraph its namespaces
  permit.

## 6. Tenant isolation

Every node and edge carries `org_id`; RLS makes cross-tenant traversal return
nothing and audit the attempt. The graph cannot bridge tenants — there is no
global graph, only per-tenant graphs
([../architecture/05_security_architecture.md §8](../architecture/05_security_architecture.md#8-tenant-isolation-defense-in-depth)).

## 7. Ownership & evolution

- **Owner:** `chief_data_officer` (governance), `director_memory_systems`
  (implementation), `director_analytics` (schema of entities/edges).
- **Evolution:** start with Postgres relations + recursive CTEs; **add a graph DB
  only if** a real traversal workload proves Postgres is the bottleneck — and even
  then behind the `MemoryRepository` port, with the event log as the rebuild
  source ([../architecture/04_memory_architecture.md §10](../architecture/04_memory_architecture.md#10-scale-migration)).
