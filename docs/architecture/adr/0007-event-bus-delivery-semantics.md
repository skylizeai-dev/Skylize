# ADR 0007 — Event Bus Delivery Semantics: At-Least-Once with Idempotent Consumers

**Status:** Proposed
**Date:** 2026-07-25
**Deciders:** Principal Architect (audit); human owner (pending acceptance)
**Register:** In-repo (`docs/architecture/adr/`). **Not** synchronised with the external
`Skylize_ADR_Register.docx` (Word) register — do not cross-reference the two.
**Related (in-repo):** [ADR-0004](./0004-opa-production-arbiter.md) · [ADR-0005](./0005-decision-engine-department-vocabulary.md) · [ADR-0006](./0006-ai-cost-ledger.md) · audit [`docs/audits/2026-07_bus_delivery_audit.md`](../../audits/2026-07_bus_delivery_audit.md)
**Code cited:** `src/skylize/events/{bus,redis_adapter,memory_bus,router}.py` · `src/skylize/schemas/base.py` · `src/skylize/decision_engine/{consumer,config,worker}.py` · `src/skylize/app/decision_engine/engine.py` · `docs/02_architecture/event_driven_architecture.md`

---

## Context

The `EventBus` port is the single sanctioned internal async channel
([`bus.py:63-69`](../../../src/skylize/events/bus.py#L63-L69)). Its delivery guarantee has
been asserted in code and docs (14 live "at-least-once" claims — audit §2a) but was, until
recently, **false**: the pre-reclaim adapter was at-*most*-once on handler failure
(recorded in `OVERNIGHT_SESSION_2026-07-19.md:431`). The reclaim work has since landed on
the shared `RedisEventBus` ([`redis_adapter.py:8-14`](../../../src/skylize/events/redis_adapter.py#L8-L14)),
making at-least-once real and test-verified
([`test_redis_bus.py:198-243`](../../../tests/integration/test_redis_bus.py#L198-L243)).

Two things remain unsettled:

1. **No single normative record** of the contract. [ADR-0006:22](./0006-ai-cost-ledger.md#L22)
   still describes bus semantics as "under active repair … not currently a guarantee we
   can bill on," and the cost ledger deliberately routes *off* the bus because of it.
2. **The architecture doc over-promises ordering.** It claims strict per-`partition_key`
   total order and a consistent-hashing consumer pin
   ([`event_driven_architecture.md:294`](../../../docs/02_architecture/event_driven_architecture.md#L294),
   [`:301-304`](../../../docs/02_architecture/event_driven_architecture.md#L301-L304),
   invariant #4 [`:407-408`](../../../docs/02_architecture/event_driven_architecture.md#L407-L408)),
   but the pin does not exist in code and the reclaim path reorders same-partition
   messages after a failure (audit §6). Because [ADR-0004](./0004-opa-production-arbiter.md)
   makes the OPA engine the production governance arbiter and preserves the six-stage model
   "unchanged" ([ADR-0004:50](./0004-opa-production-arbiter.md#L50)), which implicitly
   assumes same-partition proposals decide in order, this over-promise sits under an
   accepted decision.

This ADR records the **true** contract, makes the 14 claims normatively true, and scopes
the ordering guarantee to what the transport actually provides. It does **not** touch the
OPA-vs-inline arbiter question (that is ADR-0004's, with its unresolved cutover blockers
intact) and does not flip `SKYLIZE_DECISION_ENGINE`.

---

## Decision

### 1. Delivery guarantee — at-least-once, binding

The `EventBus` provides **at-least-once** delivery. **Every consumer MUST be idempotent on
`event_id`.** This is a binding contract, not a courtesy: a message left un-acked is
redelivered ([`redis_adapter.py:90-92`](../../../src/skylize/events/redis_adapter.py#L90-L92)),
so a non-idempotent consumer is a correctness bug. The in-memory bus models the same
guarantee for single-process runs and tests
([`memory_bus.py:9-15`](../../../src/skylize/events/memory_bus.py#L9-L15)).

Exactly-once is **not** offered and MUST NOT be assumed (see Out of Scope).

### 2. Ack policy

- **`XACK` only after the handler completes successfully**
  ([`router.py:81-86`](../../../src/skylize/events/router.py#L81-L86)). A handler that
  raises MUST NOT ack; the entry stays in the group PEL and is redelivered by the reclaim
  pass ([`router.py:97-100`](../../../src/skylize/events/router.py#L97-L100)).
- **Schema-invalid entries are acked AND mirrored to the DLQ — never silently dropped.**
  The adapter acks the poison entry and writes a `schema_rejected` DLQ record
  ([`redis_adapter.py:189-194`](../../../src/skylize/events/redis_adapter.py#L189-L194),
  [`:211-217`](../../../src/skylize/events/redis_adapter.py#L211-L217)). Trimmed/deleted
  PEL entries (no fields to decode) are acked away
  ([`redis_adapter.py:173-177`](../../../src/skylize/events/redis_adapter.py#L173-L177)).
- The adapter MUST NOT auto-ack a *live, decodable* message; acking is the consumer's
  responsibility ([`redis_adapter.py:124-125`](../../../src/skylize/events/redis_adapter.py#L124-L125)).

Codification note: this is exactly today's behaviour. Zero consumers change (audit §5).

### 3. Idempotency

- **Key:** the event's `event_id` (`str(event.event_id)`), except human-verdict resume,
  which keys on the deterministic `hitl_id` because two publications of one verdict carry
  different `event_id`s ([`consumer.py:266`](../../../src/skylize/decision_engine/consumer.py#L266),
  [`resume.py`](../../../src/skylize/decision_engine/resume.py) via `resume_dedup_key`).
- **Owner layer:** the **consumer**, via the `ProcessedEventStore` port — durable
  `PgProcessedEventStore` in the OPA worker
  ([`worker.py:130-136`](../../../src/skylize/decision_engine/worker.py#L130-L136),
  [`consumer.py:222`](../../../src/skylize/decision_engine/consumer.py#L222)), in-memory
  otherwise. `EventRouter._seen` is a second, in-process short-circuit
  ([`router.py:54`](../../../src/skylize/events/router.py#L54), [`:78-80`](../../../src/skylize/events/router.py#L78-L80));
  it is a fast-path, **not** the durable guarantee.
- **TTL:** the durable store has **no TTL** — a processed marker persists for the event's
  retention lifetime so a late reclaim cannot re-decide it. (`hitl_expiry_hours` governs
  the *decision's* human-wait window, not idempotency retention.) In-memory stores live
  for the process only, which is why production MUST use the Pg store.
- **Sink-level backstop:** decision writes derive `decision_id` from the source `event_id`
  (`uuid5`) and write with `ON CONFLICT DO NOTHING`, so a duplicate collapses even if the
  idempotency check is missed ([`engine.py:23-30`](../../../src/skylize/app/decision_engine/engine.py#L23-L30),
  [`orchestrator.py:19-24`](../../../src/skylize/decision_engine/orchestrator.py#L19-L24),
  [`dal/decision_stores.py:16`](../../../src/skylize/dal/decision_stores.py#L16)).

### 4. Retry and DLQ transition

- **Counter storage (today):** in-process, `EventRouter._attempts`
  ([`router.py:55`](../../../src/skylize/events/router.py#L55), [`:88`](../../../src/skylize/events/router.py#L88)).
- **Max attempts:** `dlq_after_retries` — the inline engine uses top-level
  `Settings.dlq_after_retries` ([`engine.py:122`](../../../src/skylize/app/decision_engine/engine.py#L122));
  the OPA worker uses `DecisionEngineSettings.redis_max_retries`
  ([`consumer.py:139`](../../../src/skylize/decision_engine/consumer.py#L139)).
- **Backoff:** none in the router; the effective interval between attempts is the reclaim
  idle window `reclaim_min_idle_ms` (`redis_idle_time_ms`, 60s) — the message is not
  re-eligible until it has been idle that long
  ([`redis_adapter.py:152-159`](../../../src/skylize/events/redis_adapter.py#L152-L159)).
- **DLQ transition:** on the attempt where `_attempts >= dlq_after`, route to
  `dlq:{tenant}:{department}` **and** ack, so the PEL does not loop
  ([`router.py:93-96`](../../../src/skylize/events/router.py#L93-L96)).
- **KNOWN, BOUNDED RESIDUAL (not fixed here):** the counter is process-memory, and the
  wire `redelivery_count` ([`base.py:69-70`](../../../src/skylize/schemas/base.py#L69-L70))
  is **never populated**. A poison message that kills every worker that touches it retries
  afresh per process and never converges to the DLQ
  ([`router.py:14-23`](../../../src/skylize/events/router.py#L14-L23),
  [`consumer.py:61-65`](../../../src/skylize/decision_engine/consumer.py#L61-L65)). Making
  the budget durable requires a delivery count carried on `DeliveredEvent` — a port change
  with a Kafka/NATS portability question ([`bus.py:76-83`](../../../src/skylize/events/bus.py#L76-L83)).
  **Out of scope here** (see Out of Scope); flagged so a later session does not assume the
  DLQ budget is crash-durable.

### 5. Reclaim

- **Mechanism:** `XAUTOCLAIM` over the group's PEL before each `">"` read
  ([`redis_adapter.py:90-92`](../../../src/skylize/events/redis_adapter.py#L90-L92),
  [`:135-177`](../../../src/skylize/events/redis_adapter.py#L135-L177)).
- **Min-idle:** `reclaim_min_idle_ms` = `redis_idle_time_ms` (default 60_000), wired from
  the worker ([`worker.py:159-164`](../../../src/skylize/decision_engine/worker.py#L159-L164)).
  It MUST stay well above normal handler latency: near-zero lets a healthy worker's
  in-flight message be stolen mid-flight
  ([`test_redis_bus.py:168-195`](../../../tests/integration/test_redis_bus.py#L168-L195)).
- **Who runs it:** every `consume` generator, i.e. every `EventRouter` loop — reclaim is
  not a separate sweeper. It is **rate-bounded** (`count=reclaim_batch`, default 16), with
  **no age ceiling** by design: an "ignore older than X" rule would silently abandon
  decisions in the PEL ([`redis_adapter.py:16-22`](../../../src/skylize/events/redis_adapter.py#L16-L22)).
- **ORDERING CONSEQUENCE (binding disclosure).** Reclaim does **not** preserve
  per-`partition_key` order. After a handler failure on `E1` (`key=p`), the `">"` read
  delivers a newer `E2` (`key=p`) and it is decided first; `E1` is reprocessed ~60s later
  (audit §6). This holds with a single worker. The consistent-hashing consumer pin the
  architecture doc cites as the ordering defence
  ([`event_driven_architecture.md:301-304`](../../../docs/02_architecture/event_driven_architecture.md#L301-L304))
  **does not exist in code**.

  **Mitigation (required):**
  1. **Monotonic partition guard at the decision sink.** A decision whose source event is
     *older* on its `partition_key` than the last-applied decision on that key MUST NOT
     overwrite the newer outcome — it is a no-op/reject. This builds on the existing
     `decision_id` + `ON CONFLICT` posture and gives capital allocation what it actually
     needs (no stale overwrite), which strict transport order was a proxy for.
  2. **Honest guarantee wording.** The guarantee is *per-`partition_key` order in the
     absence of handler failure; under failure, eventually-consistent with the
     no-stale-overwrite guard above.* The architecture doc §8/§13 and the unimplemented
     consistent-hash claim MUST be corrected to match (tracked in Consequences).
  3. A partition-scoped in-flight lock (block `">"` delivery of `key=p` while an earlier
     `key=p` entry is un-acked) is **rejected** for MVP as over-engineering; recorded in
     Out of Scope.

### 6. Canonical DLQ naming

- **Canonical:** `dlq:{tenant}:{department}` ([`bus.py:32-34`](../../../src/skylize/events/bus.py#L32-L34);
  arch doc §2 [`:40`](../../../docs/02_architecture/event_driven_architecture.md#L40),
  §9 [`:331`](../../../docs/02_architecture/event_driven_architecture.md#L331)).
- **Deprecated:** `evt:dlq:decision_engine` (`redis_dlq_stream`,
  [`config.py:21`](../../../src/skylize/decision_engine/config.py#L21)) — a single global,
  wrongly `evt:`-prefixed name, read by no production code (audit §3, §4).
- **Migration path:** delete `redis_dlq_stream` (and `redis_batch_size`) from
  `DecisionEngineSettings` and its test fixture; nothing production reads them. DLQ triage
  tooling iterates per (tenant, department). This ADR authorises that deletion; it is
  executed by the implementation terminal, not here.

---

## Out of scope (a later session MUST NOT silently expand this ADR to cover these)

- **Exactly-once delivery / dedup at the transport.** Idempotency lives in the consumer,
  not the bus.
- **Durable / crash-surviving retry budget** and populating the wire `redelivery_count`
  ([`base.py:69-70`](../../../src/skylize/schemas/base.py#L69-L70)). Requires a
  `DeliveredEvent` port change ([`bus.py:76-83`](../../../src/skylize/events/bus.py#L76-L83));
  disclosed in Decision §4 as a known residual, not resolved.
- **Partition-scoped in-flight locking** for strict order under failure (Decision §5.3).
- **S3 cold archive / replay** (`event_driven_architecture.md:35-36`, §10) — not part of
  the hot-path delivery contract.
- **`SKYLIZE_DECISION_ENGINE` cutover** and the OPA-vs-inline arbiter question — owned by
  [ADR-0004](./0004-opa-production-arbiter.md), including its unresolved cutover blockers.
- **The `RedisGovernanceBroadcast` Pub/Sub kill-switch** — a different transport,
  intentionally at-most-once ([`app/governance/broadcast.py:12`](../../../src/skylize/app/governance/broadcast.py#L12)).

---

## Consequences

- **The 14 live at-least-once claims (audit §2a) become normatively backed** by this ADR
  rather than by scattered docstrings.
- **Consumers that must remain idempotent on `event_id`** (each already is; this pins the
  requirement):
  - Inline `DecisionEngine` — [`engine.py:154`](../../../src/skylize/app/decision_engine/engine.py#L154) (production default).
  - OPA `DecisionEngineConsumer` — [`consumer.py:222`](../../../src/skylize/decision_engine/consumer.py#L222), [`:284`](../../../src/skylize/decision_engine/consumer.py#L284) (dormant behind ADR-0004's flag).
  - `EventRouter` shared mechanism — [`router.py:54`](../../../src/skylize/events/router.py#L54), [`:78-80`](../../../src/skylize/events/router.py#L78-L80).
- **Docs to correct (implementation terminal):** `event_driven_architecture.md` §8/§13
  ordering wording + delete the unimplemented consistent-hash pin claim; note that
  `redelivery_count` is not currently populated (or implement it under the separate
  port-change work); reconcile [ADR-0006:22](./0006-ai-cost-ledger.md#L22) to point at
  this ADR as the now-settled contract.
- **Code the implementation terminal will touch (this audit touches NONE):**
  - `src/skylize/decision_engine/config.py` — delete `redis_dlq_stream`, `redis_batch_size`.
  - `src/skylize/decision_engine/publisher.py` (or the decision sink) + a migration — add the monotonic per-`partition_key` guard (Decision §5.1).
  - `docs/02_architecture/event_driven_architecture.md` — §8/§13 ordering wording; §3/§9 `redelivery_count`.
  - `docs/architecture/adr/0006-ai-cost-ledger.md` — cross-link this ADR.
  - test fixtures referencing the deleted settings (`tests/decision_engine/conftest.py`).
  - **Optionally** (separate ADR/port change): `events/bus.py` + `events/router.py` for a durable delivery count.
- **No source or test file is modified by this ADR or its audit** — documentation-only,
  by design and by the audit's hard exit gate.

---

## Alternatives considered

- **Declare exactly-once.** Rejected: not achievable over Redis Streams without a dedup
  layer the bus deliberately pushes to consumers; the port carries no delivery count
  ([`bus.py:76-83`](../../../src/skylize/events/bus.py#L76-L83)).
- **Claim strict per-`partition_key` order (keep the doc as-is).** Rejected: the code does
  not provide it under failure (audit §6); claiming it is the exact doc-vs-code drift this
  ADR exists to end. Honesty + a no-stale-overwrite guard is the defensible contract.
- **Fix the durable retry budget now.** Rejected for this ADR: it is a `DeliveredEvent`
  port change with a cross-adapter portability question; bundling it would expand scope
  past "record the contract." Disclosed as a bounded residual instead.
- **Leave the contract implicit in docstrings.** Rejected: same reasoning as ADR-0001/0002
  — a governance-critical guarantee must be one explicit, cross-linked record, especially
  when an accepted decision (ADR-0004) rests on it.
