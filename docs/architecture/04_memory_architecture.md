# 04 — Memory Architecture

**Status:** Production architecture (source of truth)
**Owner:** Principal Architect
**Related:** [01_final_stack.md](./01_final_stack.md) · [03_agent_runtime.md](./03_agent_runtime.md) · [event_driven_architecture.md](../02_architecture/event_driven_architecture.md) · [agent_governance.md](../03_agents/agent_governance.md) · existing [docs/05_memory/](../05_memory/)

---

## 1. Purpose

How Skylize remembers: the memory tiers, their stores, how agents read and write
memory under contract scope, and how memory stays tenant-isolated, consistent,
and replayable. This consolidates the existing [docs/05_memory/](../05_memory/)
notes into a final, store-backed architecture.

---

## 2. Principles

1. **Memory is scoped, never global.** Every read/write is gated by the agent's
   `memory_read_access` / `memory_write_access` namespaces
   ([agent_contract_registry.md §2](../03_agents/agent_contract_registry.md#2-the-contract-schema))
   and isolated by `org_id`.
2. **Workers propose; the system persists.** Agents emit `memory.write_requested`
   events; the Memory service commits — agents do not write stores directly
   (consistent with the agent sandbox in
   [03_agent_runtime.md §4](./03_agent_runtime.md#4-the-agent-sandbox-if-agent)).
3. **The event log is the source of truth.** All memory state can be rebuilt by
   replaying `MemoryEvent`s
   ([event_driven_architecture.md](../02_architecture/event_driven_architecture.md)).
4. **Right store for each access pattern** — no single store forced to do
   everything (see overlap rejections in
   [01_final_stack.md §3](./01_final_stack.md#3-rejected-overlaps-one-tool-per-job)).

---

## 3. Memory tiers

| Tier | Lifetime | Store | Access pattern |
|---|---|---|---|
| **Working memory** | per run | LangGraph state (Postgres checkpoint) + Redis | scratchpad within one workflow |
| **Episodic memory** | medium | Postgres (events/outcomes) + Qdrant (embeddings) | "what happened, and how did it go" |
| **Semantic memory** | long | Qdrant (vectors) + Postgres (canonical text) | similarity recall of facts, patterns, brand voice |
| **Procedural memory** | long | Postgres | learned playbooks, approved workflows, policies |
| **Organizational memory / knowledge graph** | long | Postgres relations (+ Qdrant for fuzzy) | entities and relationships across the company |
| **Audit memory** | 7y, immutable | Redis (hot) → S3 Parquet (object-lock) | compliance / replay (never mutated) |

This maps the existing [memory_taxonomy.md](../05_memory/memory_taxonomy.md),
[organizational_memory.md](../05_memory/organizational_memory.md),
[knowledge_graph.md](../05_memory/knowledge_graph.md), and
[retrieval_strategy.md](../05_memory/retrieval_strategy.md) onto concrete stores.

---

## 4. Store assignment & rationale

### 4.1 Qdrant — semantic / episodic vectors (primary)
- **Why:** self-hostable, payload filtering (essential for `tenant:{org_id}` +
  namespace scoping in one query), strong recall/latency.
- **Namespacing:** one logical collection per tenant scope, payload-filtered by
  `org_id` + memory namespace; physical sharding by tenant at Scale.
- **Alternatives:** Pinecone (managed lock-in), Weaviate/Milvus (heavier ops).
- **Migration path:** behind the `VectorStore` port — swap to pgvector (small
  tenants/dev) or another engine via adapter; embeddings are re-indexable from
  the Postgres canonical text + event log.

### 4.2 PostgreSQL — canonical text, relations, knowledge graph, procedural
- **Why:** transactional source of record; JSONB for flexible payloads; FTS for
  keyword recall; RLS for tenant isolation; relations express the knowledge graph
  without a separate graph DB (deferred until traversal is a proven bottleneck —
  see [01_final_stack.md §3](./01_final_stack.md#3-rejected-overlaps-one-tool-per-job)).
- **Migration path:** commodity wire; pgvector available in-engine as a vector
  fallback, so a tenant can run vectorless-of-Qdrant if needed.

### 4.3 Redis — working memory & recall cache
- **Why:** already present; sub-ms scratchpad and hot recall cache. Volatile by
  design; never the source of truth.

### 4.4 S3 (MinIO/AWS) — large artifacts & immutable audit archive
- **Why:** generated creative assets and the object-locked audit/governance
  archive (7-year retention floor in
  [event_driven_architecture.md §11](../02_architecture/event_driven_architecture.md#11-retention-policy-per-event-type)).
- **Migration path:** S3 API commodity (MinIO ↔ AWS ↔ R2).

---

## 5. The Memory service & ports

Agents reach memory only through the **Memory service**, behind two ports so
stores are swappable:

```python
class VectorStore(Protocol):
    async def upsert(self, namespace: str, org_id: str, items: list[VectorItem]) -> None: ...
    async def search(self, namespace: str, org_id: str, query, k: int, filters) -> list[Hit]: ...

class MemoryRepository(Protocol):   # over Postgres via the DAL
    async def write(self, namespace: str, org_id: str, record) -> RecordId: ...
    async def read(self, namespace: str, org_id: str, selector) -> list[Record]: ...
```

`org_id` is a required parameter on every method — there is no un-scoped call.

---

## 6. Read path (recall)

1. Agent calls `memory.search` (a tool in its `allowed_tools`).
2. Tool proxy validates the `GovernanceToken` and that the requested namespace ∈
   `memory_read_access`.
3. Memory service does **hybrid retrieval**: Qdrant vector search (payload-filtered
   by `org_id` + namespace) + Postgres FTS/relational lookup, fused and re-ranked
   per [retrieval_strategy.md](../05_memory/retrieval_strategy.md).
4. Emits `memory.recall_served`; results returned to the agent.

A read outside the agent's `memory_read_access` is denied at the proxy →
`AuditEvent(audit.access_denied)`.

---

## 7. Write path (commit)

Writes are **event-sourced**, so they are auditable and replayable:

1. Agent emits `memory.write_requested` (only if namespace ∈ `memory_write_access`;
   workers with empty write access cannot, e.g. `hook_generator_agent`).
2. Memory service validates scope + tenant, writes canonical text to Postgres
   (transactional), enqueues embedding.
3. On embedding done → upsert to Qdrant → `memory.embedding_indexed`.
4. `memory.committed` confirms; an `AuditEvent` records who wrote what (PII-safe
   hashes).
5. Updates that supersede prior memory emit `memory.invalidated` for the stale
   record.

Because Postgres is committed before the vector index, the canonical store is the
arbiter; Qdrant can always be rebuilt from it + the event log.

---

## 8. Consistency model

- **Postgres = source of truth** (strong, transactional).
- **Qdrant = derived index** (eventually consistent; rebuildable).
- **Redis = volatile cache** (best-effort; invalidate on `memory.invalidated`).
- A reconciliation job replays `memory.*` events to detect/repair vector drift.

This gives strong consistency where it matters (canonical record, audit) and
acceptable eventual consistency where it is cheap to rebuild (vector index,
cache).

---

## 9. Tenant isolation & learning

- Every store enforces `org_id`: Postgres RLS, Qdrant payload filter + (Scale)
  shard, S3 prefix. A cross-tenant read returns nothing and audits the attempt.
- **Cross-tenant learning is opt-in and anonymized only.** Default is strict
  per-tenant memory; any aggregate/global pattern learning (see
  [learning_pipeline.md](../05_memory/learning_pipeline.md)) operates on
  de-identified, consented data through a separate, governed pipeline — never by
  reading another tenant's namespace.

---

## 10. Scale migration

| Concern | MVP | Scale | Trigger |
|---|---|---|---|
| Vectors | Qdrant single node (or pgvector) | Qdrant cluster sharded by tenant | recall latency / index size |
| Canonical store | single Postgres | HA + read replicas; shard by `org_id` | write contention |
| Knowledge graph | Postgres relations | add graph DB **only if** traversal is the bottleneck | proven query cost |
| Audit archive | S3 standard | S3 + lifecycle to Glacier | retention cost |

All moves are substitutions behind the `VectorStore` / `MemoryRepository` ports
and the DAL — no agent or domain change.
