# Event-Driven Architecture

**Status:** Foundation document (source of truth)
**Owner:** Chief Systems Architect
**Related:** [system_boundaries.md](./system_boundaries.md) · [agent_governance.md](../03_agents/agent_governance.md) · [agent_contract_registry.md](../03_agents/agent_contract_registry.md)

---

## 1. Purpose

The event bus is the nervous system of Skylize. It is the single sanctioned
asynchronous channel across the Event Boundary (`IF-EVENT` in
[system_boundaries.md §4.5](./system_boundaries.md#45-event-boundary--interface-if-event)).
Every agent output, every decision, every memory mutation, every governance
action, and every audit record flows through it as a typed, versioned,
ordered, replayable event.

Design goals: **typed** (Pydantic v2, versioned), **ordered** (per partition
key), **durable** (Redis Streams + cold archive), **replayable** (for debugging
and compliance), **observable** (Langfuse + OpenTelemetry on every hop), and
**isolated** (per-tenant).

---

## 2. Why Redis Streams as the primary bus

Redis Streams gives us: append-only logs with monotonic IDs (native ordering),
consumer groups with acknowledgement and pending-entries tracking (at-least-once
delivery + DLQ semantics), `XAUTOCLAIM` for stuck-message recovery, and
range/replay reads (`XRANGE`) for debugging. We already run Redis for cache/queue
(see [system_boundaries.md §4.4](./system_boundaries.md#44-data-boundary--interface-if-data)),
so it adds no new infrastructure for v1.

**Topology:** one stream per **department channel**, partitioned by tenant. Cold
events are continuously archived to S3 (Parquet) for long-horizon replay and
compliance, beyond the hot Redis retention window.

```
Stream naming:   evt:{tenant}:{department}        e.g. evt:org_123:creative
DLQ naming:      dlq:{tenant}:{department}
Consumer group:  cg:{consumer_name}               e.g. cg:decision_engine
Cold archive:    s3://skylize/{tenant}/events/{department}/{date}/*.parquet
```

The bus is provider-abstracted behind an `EventBus` port so a future migration to
Kafka/NATS for a department needs no producer/consumer code changes — only an
adapter swap.

---

## 3. Event Schema Standard (Pydantic v2, versioned)

Every event is a Pydantic v2 model inheriting from `BaseEvent`. The **envelope**
is invariant across all event types; the **payload** is type-specific and
independently versioned.

```python
from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4
from pydantic import BaseModel, Field, ConfigDict


class EventCategory(str, Enum):
    CREATIVE = "creative"
    SALES = "sales"
    MEMORY = "memory"
    DECISION = "decision"
    GOVERNANCE = "governance"
    AUDIT = "audit"


class BaseEvent(BaseModel):
    """Invariant envelope for every event crossing IF-EVENT."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Identity & versioning
    event_id: UUID = Field(default_factory=uuid4)
    schema_version: str = Field(..., pattern=r"^\d+\.\d+$")  # e.g. "1.0"
    category: EventCategory
    type: str  # dotted, e.g. "creative.hooks_generated"

    # Routing & ordering
    tenant_id: str                      # org_id; partitions every stream
    partition_key: str                  # ordering key (see §8)
    department: str                     # owning department channel

    # Provenance (links to governance & agents)
    source_agent_id: str | None = None
    authority_level: Literal[
        "executive", "vp", "director", "manager", "worker"
    ] | None = None                     # IDENTICAL set to governance/registry
    governance_token_id: UUID | None = None  # which token authorized this
    causation_id: UUID | None = None    # the event that caused this one
    correlation_id: UUID                # ties a whole workflow together

    # Timing
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # Delivery metadata (set by the bus, not the publisher)
    redelivery_count: int = 0
```

**Versioning rules:**
- `schema_version` is `MAJOR.MINOR`. **Minor** = additive, backward-compatible
  (new optional fields). **Major** = breaking; both versions run in parallel
  during migration and consumers declare the versions they accept.
- A consumer that receives an unknown **major** version routes the event to the
  DLQ (`type=audit.schema_rejected`) rather than guessing.
- The envelope (`BaseEvent`) is frozen and changes only by org-wide RFC.

A concrete event extends the envelope with a typed payload:

```python
class CreativeHooksGenerated(BaseEvent):
    category: Literal[EventCategory.CREATIVE] = EventCategory.CREATIVE
    type: Literal["creative.hooks_generated"] = "creative.hooks_generated"
    schema_version: Literal["1.0"] = "1.0"

    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        brief_id: UUID
        hooks: list[str]
        model_used: str
        token_cost: int

    payload: "CreativeHooksGenerated.Payload"
```

---

## 4. Event Validation at the Boundary

Publishing is mediated by the `EventBus.publish()` port, which **always**:
1. Validates the model (Pydantic v2). Invalid → reject, route to DLQ,
   emit `AuditEvent(type="audit.schema_rejected")`. Never silently dropped
   (see [system_boundaries.md §4.5](./system_boundaries.md#45-event-boundary--interface-if-event)).
2. Stamps `occurred_at` and enforces presence of `correlation_id` and
   `partition_key`.
3. Writes to `evt:{tenant}:{department}` via `XADD`.
4. Mirrors an `AuditEvent` for governance-relevant categories.

---

## 5. Event Taxonomy

Six top-level categories. Each department publishes within its category; the
`type` is a dotted verb-phrase. This taxonomy is closed — adding a `type`
requires registering its payload schema in the event registry.

### CreativeEvent — `category=creative`
Department: Creative Production (VP Creative org).
- `creative.brief_received`
- `creative.hooks_generated`
- `creative.copy_drafted`
- `creative.variant_produced`
- `creative.asset_rendered`
- `creative.review_requested`
- `creative.asset_approved` / `creative.asset_rejected`

### SalesEvent — `category=sales`
Departments: Sales Intelligence, Growth.
- `sales.lead_enriched`
- `sales.signal_detected`
- `sales.campaign_proposed`
- `sales.campaign_launched`
- `sales.performance_ingested`
- `sales.budget_reallocation_proposed`

### MemoryEvent — `category=memory`
Cross-cutting (vector + structured memory).
- `memory.write_requested`
- `memory.committed`
- `memory.embedding_indexed`
- `memory.recall_served`
- `memory.invalidated`

### DecisionEvent — `category=decision`
Owner: Decision Engine (see §7).
- `decision.evaluated`
- `decision.approved`
- `decision.rejected`
- `decision.deferred_to_human`
- `decision.conflict_detected`
- `decision.conflict_resolved`

### GovernanceEvent — `category=governance`
Owner: Governance Authority
(see [agent_governance.md](../03_agents/agent_governance.md)).
- `governance.token_issued`
- `governance.token_revoked`
- `governance.scope_violation`
- `governance.circuit_breaker_tripped`
- `governance.agent_suspended` / `governance.agent_reinstated`
- `governance.kill_switch_engaged`
- `governance.human_escalation_raised`
- `governance.edge_block` / `governance.integration_bad_signature`

### AuditEvent — `category=audit`
Owner: Audit subsystem. Immutable, append-only, the compliance spine.
- `audit.action_recorded`
- `audit.access_denied`
- `audit.data_access`
- `audit.schema_rejected`
- `audit.replay_executed`

Every other category's significant transitions are mirrored as `AuditEvent`s, so
the audit stream alone can reconstruct system history.

---

## 6. Publisher & Subscriber Contracts per Department

Each department declares, in code, a **PublisherContract** (events it may emit)
and **SubscriberContract** (events it consumes + its consumer group). The
Orchestrator enforces that an agent only publishes types its department's
contract permits — an agent emitting outside its department's contract is a
governance violation (see [agent_governance.md §6](../03_agents/agent_governance.md#6-circuit-breaker-rules)).

```python
class PublisherContract(BaseModel):
    department: str
    emits: set[str]            # allowed event `type`s

class SubscriberContract(BaseModel):
    department: str
    consumer_group: str        # cg:{name}
    consumes: set[str]         # subscribed `type`s
    accepts_major_versions: dict[str, set[int]]  # type -> {1, 2}
    dlq_after_retries: int = 5
```

| Department | Publishes (sample) | Consumes (sample) |
|---|---|---|
| Creative Production | `creative.*` | `decision.approved`, `sales.campaign_proposed` |
| Sales Intelligence | `sales.lead_enriched`, `sales.signal_detected` | `decision.approved`, `memory.recall_served` |
| Growth | `sales.campaign_proposed`, `sales.budget_reallocation_proposed` | `sales.performance_ingested`, `decision.approved` |
| Decision Engine | `decision.*` | `creative.review_requested`, `sales.*`, `governance.*` |
| Governance Authority | `governance.*` | `audit.*`, `decision.conflict_detected` |
| Memory | `memory.*` | `creative.asset_approved`, `sales.signal_detected` |
| Audit | `audit.*` | **all** (mirror consumer) |

### How agents publish outputs as events
An agent never writes to a stream directly. It returns a typed output object
(its contract's `output_schema`, see
[agent_contract_registry.md](../03_agents/agent_contract_registry.md)). The
Orchestrator wraps that output in the correct event model, stamps provenance
(`source_agent_id`, `authority_level`, `governance_token_id`, `causation_id`,
`correlation_id`), validates against the department PublisherContract, and calls
`EventBus.publish()`. This keeps the Agent Boundary sandboxed (`IF-AGENT`) while
making agent output first-class on the bus.

---

## 7. The Decision Engine

The Decision Engine is the central consumer/producer that turns agent *intent*
into authorized *outcomes*. It is part of the Application Boundary
(see [system_boundaries.md §4.2](./system_boundaries.md#42-application-boundary--interfaces-if-agent-if-data-if-event)).

**Consumes:** `creative.review_requested`, all `sales.*` proposals,
`decision.conflict_detected`, and relevant `governance.*`.

**Emits:** `decision.evaluated`, then exactly one terminal outcome:
`decision.approved`, `decision.rejected`, or `decision.deferred_to_human`.

**Flow per inbound proposal:**
1. Read from `cg:decision_engine` on the department stream.
2. Load policy + governance state (circuit breaker / kill switch / token
   validity) from the Governance Authority.
3. Check the originating agent's `authority_level` against what the action
   requires (see [governance authority matrix](../03_agents/agent_governance.md#3-authority--escalation)).
   - Within authority → evaluate against policy → `approved`/`rejected`.
   - Exceeds authority → `decision.deferred_to_human` and a
     `governance.human_escalation_raised` along the agent's `escalation_path`.
4. On conflict between two agents (overlapping mandates) → emit
   `decision.conflict_detected`, run conflict resolution
   (see [agent_governance.md §9](../03_agents/agent_governance.md#9-conflict-resolution)),
   then `decision.conflict_resolved`.
5. `ACK` the message. On unhandled exception → no ACK → retried → DLQ after the
   department's `dlq_after_retries`.

Every step emits a mirrored `AuditEvent`, and `correlation_id` ties the entire
chain so a proposal → decision → action is fully traceable.

---

## 8. Ordering Guarantees

- **Per `partition_key`: strict total order.** Redis Streams assigns monotonic
  IDs within a stream; we choose `partition_key` so causally-related events share
  a stream and key. Standard keys: `campaign:{id}`, `brief:{id}`,
  `agent:{agent_id}`, `lead:{id}`.
- **Across partitions: no global order is promised.** Consumers must rely on
  `causation_id`/`correlation_id`, not wall-clock or cross-stream arrival, to
  reconstruct cross-entity causality.
- **Single consumer per key within a group:** a consumer group delivers any one
  message to one consumer; we additionally pin a `partition_key` to a consumer
  via consistent hashing so per-key processing stays ordered even with horizontal
  scaling.
- **Idempotency:** consumers must be idempotent on `event_id` (at-least-once
  delivery means a message can be redelivered after a crash before ACK).
- **Reclaim is rate-bounded, not age-bounded.** `RedisEventBus.consume` runs one
  bounded `XAUTOCLAIM` batch before each `">"` read, so the first start after
  reclaim was introduced drains whatever had accumulated in the PEL steadily
  rather than as a single flood. There is deliberately **no age ceiling**: an
  "ignore entries older than X" rule would silently abandon decisions sitting in
  the PEL, which is the exact failure reclaim exists to prevent. Most of a
  first-start backlog is cheap to drain anyway — entries whose outcome was
  already recorded are short-circuited by the consumer's `ProcessedEventStore`
  and acked without re-running the pipeline. Entries that were never processed
  *are* processed, which is the point of the change.
- **Staleness is a policy question, not a transport one.** A proposal reclaimed
  long after publication is decided on its merits, because the transport has no
  basis for judging whether age invalidates it. If a department needs proposals
  to expire, that belongs in the pipeline's own stages (as `hitl_expiry_hours`
  already does for deferred decisions), not in the bus.
- **The reclaim window is not a retry interval.** `redis_idle_time_ms` (60s)
  says how long a message must look abandoned before a peer may take it. Setting
  it near zero would let a sibling steal messages from a healthy worker
  mid-flight, turning every delivery into a duplicate.

---

## 9. Dead Letter Queue Strategy

- Each department stream has a paired DLQ `dlq:{tenant}:{department}`.
- A message is DLQ'd when: schema validation fails, an unknown major version is
  seen, or processing fails `dlq_after_retries` times (tracked via the consumer
  group's pending-entries list and `redelivery_count`).
- `XAUTOCLAIM` reclaims messages stuck in a dead consumer's PEL after an idle
  timeout, incrementing `redelivery_count`; exceeding the retry budget routes to
  DLQ.
- Every DLQ insertion emits `audit.action_recorded` + a
  `governance.*` signal if governance-relevant, and increments a Langfuse/OTel
  counter that feeds alerting.
- **DLQ is never auto-purged.** Operators triage via a tooling console: inspect,
  patch (e.g., re-version), and **replay** (§10) or discard with an audited
  reason.

---

## 10. Event Replay (debugging & compliance)

Replay reconstructs state or re-runs processing from the durable log.

- **Source:** hot replay from Redis (`XRANGE` within retention) or cold replay
  from the S3 Parquet archive for older windows.
- **Scoped selectors:** by `tenant_id`, `correlation_id`, `partition_key`,
  `type`, or time range.
- **Two modes:**
  - *Shadow replay* — events are fed to an isolated consumer that writes to a
    sandbox; production state untouched. Used for debugging and "what would the
    Decision Engine do now" analysis.
  - *Authoritative replay* — used only for recovery, behind a kill-switch-style
    human approval, and itself recorded as `audit.replay_executed`.
- **Compliance:** because every governance and audit event is retained, replay
  can reproduce, for any tenant and time window, exactly which agent did what,
  under which governance token, and why a decision was made.

---

## 11. Retention Policy per Event Type

| Category | Hot (Redis) | Cold archive (S3 Parquet) | Rationale |
|---|---|---|---|
| `audit` | 30 days | **7 years**, immutable (object-lock) | Compliance / legal |
| `governance` | 30 days | 7 years | Security & accountability |
| `decision` | 30 days | 2 years | Explainability of outcomes |
| `sales` | 14 days | 13 months | Performance analysis YoY |
| `creative` | 14 days | 13 months | Asset lineage |
| `memory` | 7 days | 90 days | Memory is materialized elsewhere |

- Hot trimming via `XADD ... MAXLEN`/time-based `XTRIM`; an archiver consumer
  ships to S3 before trimming, so nothing is lost on rollover.
- `audit` and `governance` cold storage uses write-once object lock — they cannot
  be altered or deleted within the retention window, satisfying the immutability
  invariant in
  [system_boundaries.md §4.4](./system_boundaries.md#44-data-boundary--interface-if-data).
- Retention is per-tenant configurable **upward** (never below the compliance
  floor for `audit`/`governance`).

---

## 12. Observability

Every publish and consume is instrumented:
- **OpenTelemetry** spans carry `correlation_id` and `event_id`, so a workflow is
  one distributed trace from edge request to final decision.
- **Langfuse** captures LLM tool calls referenced by `governance_token_id`,
  linking model cost/quality back to the agent and event that triggered it.
- **Structured logs** include the full envelope (minus payload PII) for every hop.

---

## 13. Invariants (must always hold)

1. The bus is the only sanctioned internal async channel (no back-channels).
2. Every event validates against a versioned Pydantic v2 schema or it goes to the
   DLQ — never silently dropped.
3. Agents never publish directly; the Orchestrator wraps validated agent output
   with provenance and publishes.
4. Per-`partition_key` ordering is guaranteed; cross-partition causality is
   carried by `causation_id`/`correlation_id`.
5. `audit` and `governance` events are immutable and retained at the compliance
   floor.
6. Any state change can be reconstructed by replaying the durable log.
