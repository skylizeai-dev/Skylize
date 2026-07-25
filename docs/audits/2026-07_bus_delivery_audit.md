# Event Bus Delivery-Semantics Audit — 2026-07

**Auditor:** Principal Architect (adversarial delivery-semantics review)
**Scope:** `RedisEventBus` / `InMemoryEventBus` / `EventRouter` and every consumer of the `EventBus` port.
**Method:** read-only. Code is ground truth; every claim below carries a `file:line`.
**Branch under audit:** `worktree-bus-audit-gov`, based on `feat/durable-governance` tip `244e2857`.
**Companion decision:** [ADR-0007 — Event Bus Delivery Semantics](../architecture/adr/0007-event-bus-delivery-semantics.md).

Register note: ADR numbers in this document refer to the **in-repo** register
(`docs/architecture/adr/`). They are **not** synchronised with the external
`Skylize_ADR_Register.docx` (Word) register and must not be cross-referenced to it.

---

## 0. Headline finding — the accepted arbiter rests on a partially-unmet delivery precondition

State this first because it changes how ADR-0004 should be read.

[ADR-0004](../architecture/adr/0004-opa-production-arbiter.md) (Accepted, 2026-07-17)
makes the OPA/Rego engine the **production governance arbiter**, on the owner's stated
grounds that *"a governance product must not silently drop decisions"*
([`decision_engine/consumer.py:56-58`](../../src/skylize/decision_engine/consumer.py#L56-L58)).
That posture has two delivery preconditions. One is now met; one is not.

- **MET — at-least-once delivery.** The `RedisEventBus` now reclaims stalled PEL
  entries with `XAUTOCLAIM` before each `">"` read
  ([`redis_adapter.py:8-14`](../../src/skylize/events/redis_adapter.py#L8-L14),
  [`redis_adapter.py:90-92`](../../src/skylize/events/redis_adapter.py#L90-L92)),
  so an un-acked message is redelivered. This is verified end-to-end against real
  Redis ([`tests/integration/test_redis_bus.py:198-243`](../../tests/integration/test_redis_bus.py#L198-L243)).
  Historically this was **false** — the pre-reclaim adapter was at-*most*-once on failure
  (`OVERNIGHT_SESSION_2026-07-19.md:431`), and the claim was corrected in eight places
  before the machinery existed (`OWNER_DECISIONS_QUEUE_2026-07-19.md:194`). It is now real.

- **NOT MET — per-`partition_key` ordering under redelivery.** The architecture doc
  promises *strict total order per `partition_key`*
  ([`event_driven_architecture.md:294`](../../docs/02_architecture/event_driven_architecture.md#L294),
  invariant #4 at [`:407-408`](../../docs/02_architecture/event_driven_architecture.md#L407-L408))
  and a *consistent-hashing pin of `partition_key` → consumer*
  ([`:301-304`](../../docs/02_architecture/event_driven_architecture.md#L301-L304)).
  **Neither is implemented** (see §6). The reclaim path reprocesses a failed earlier
  message *after* a later message on the same `partition_key` has already been decided.
  For the spend-bearing proposals ADR-0004/0005 exist to govern (capital allocation,
  budget reallocation), that is a governance-correctness risk, not a cosmetic one (§6).

**Consequence for ADR-0004:** the arbiter decision is safe on the *no-silent-drop*
axis it was argued on, but the six-stage evaluation model it preserves unchanged
([`ADR-0004:50`](../architecture/adr/0004-opa-production-arbiter.md#L50)) implicitly
assumes same-partition proposals are decided in order — and that assumption is **not**
upheld by today's transport. ADR-0007 records the true contract and the mitigation.

This is a **doc-vs-code conflict** (arch doc vs transport), not an ADR-vs-ADR conflict.
Per `STOP_ON_ARCHITECTURE_CONFLICT` it is **reported here, not resolved unilaterally**;
ADR-0007 scopes the delivery guarantee to what the code actually provides and names the
ordering gap as a bounded, owner-visible residual rather than silently claiming order holds.

---

## 1. Current-state table — claimed vs actual guarantee, per code path

| Code path | Claimed guarantee | Actual behaviour (code) | Evidence |
|---|---|---|---|
| `RedisEventBus.publish` | Validate → DLQ on invalid, never drop ([`bus.py:85-90`](../../src/skylize/events/bus.py#L85-L90); arch §4 [`:138-143`](../../docs/02_architecture/event_driven_architecture.md#L138-L143)) | XADD only. **No validation on publish** — the arg is already a typed `BaseEvent`; schema rejection happens on the *consume* side, not here | [`redis_adapter.py:65-69`](../../src/skylize/events/redis_adapter.py#L65-L69) |
| `RedisEventBus.consume` (new messages) | at-least-once ([`bus.py:68-69`](../../src/skylize/events/bus.py#L68-L69)) | `XREADGROUP` with `">"` into the group PEL; entries are **not** auto-removed | [`redis_adapter.py:109-113`](../../src/skylize/events/redis_adapter.py#L109-L113) |
| `RedisEventBus.consume` (reclaim) | redelivery of un-acked ([`redis_adapter.py:8-14`](../../src/skylize/events/redis_adapter.py#L8-L14)) | `XAUTOCLAIM` (min-idle `reclaim_min_idle_ms`, `count=reclaim_batch`) before each `">"` read; rate-bounded, no age ceiling | [`redis_adapter.py:90-92`](../../src/skylize/events/redis_adapter.py#L90-L92), [`:135-177`](../../src/skylize/events/redis_adapter.py#L135-L177) |
| `RedisEventBus.ack` | removes from PEL ([`bus.py:104-106`](../../src/skylize/events/bus.py#L104-L106)) | `XACK`. Never auto-called by the adapter for live messages | [`redis_adapter.py:124-125`](../../src/skylize/events/redis_adapter.py#L124-L125) |
| Schema-invalid entry | acked **and** mirrored to DLQ, never dropped (arch §4 [`:138-143`](../../docs/02_architecture/event_driven_architecture.md#L138-L143)) | `_admit` acks the entry **and** writes a `schema_rejected` DLQ mirror | [`redis_adapter.py:189-194`](../../src/skylize/events/redis_adapter.py#L189-L194), [`:211-217`](../../src/skylize/events/redis_adapter.py#L211-L217) |
| Trimmed/deleted PEL entry | — | acked away (fields empty, nothing to decode) | [`redis_adapter.py:173-177`](../../src/skylize/events/redis_adapter.py#L173-L177) |
| `EventRouter._dispatch` (success) | ack after success | handler → mark seen → `XACK` | [`router.py:81-86`](../../src/skylize/events/router.py#L81-L86) |
| `EventRouter._dispatch` (failure) | no-ack → retry → DLQ after budget | on exception: **no ack**; increment in-process `_attempts`; at `>= dlq_after` → `to_dlq` + ack | [`router.py:87-100`](../../src/skylize/events/router.py#L87-L100) |
| Retry / DLQ budget | tracked via `redelivery_count` on the wire (arch §9 [`:333-337`](../../docs/02_architecture/event_driven_architecture.md#L333-L337)) | tracked in **process memory** (`_attempts` dict); does not survive restart or a different reclaiming worker | [`router.py:14-23`](../../src/skylize/events/router.py#L14-L23), [`:55`](../../src/skylize/events/router.py#L55) |
| `redelivery_count` wire field | "set by the bus" (arch §3 [`:104-105`](../../docs/02_architecture/event_driven_architecture.md#L104-L105); [`base.py:43`](../../src/skylize/schemas/base.py#L43)) | field exists, default `0`, **never set or incremented anywhere** | [`base.py:69-70`](../../src/skylize/schemas/base.py#L69-L70) (only definition; no writer in repo) |
| Per-`partition_key` order | strict total order + consistent-hash consumer pin (arch §8 [`:294`](../../docs/02_architecture/event_driven_architecture.md#L294), [`:301-304`](../../docs/02_architecture/event_driven_architecture.md#L301-L304)) | **no pinning code exists**; `">"` delivers a newer same-key message while an earlier one waits in the PEL, so reclaim reorders | §6 below; no match for hash/pin in `events/` |
| `InMemoryEventBus` | at-least-once, honest model | redelivers oldest un-acked when stream drained; idle window collapsed to zero | [`memory_bus.py:9-15`](../../src/skylize/events/memory_bus.py#L9-L15), [`:65-82`](../../src/skylize/events/memory_bus.py#L65-L82) |

**Net delivery verdict:** **at-least-once** (un-acked messages *are* redelivered),
with two qualifications that are NOT delivery-count properties and must not be conflated
with the guarantee: (a) per-`partition_key` ordering is **not** preserved across a
reclaim (§6); (b) the retry-to-DLQ budget is **non-durable** (process-memory counter;
the wire `redelivery_count` is inert), so a worker-killing poison pill retries afresh per
process instead of converging to the DLQ ([`router.py:14-23`](../../src/skylize/events/router.py#L14-L23),
[`consumer.py:61-65`](../../src/skylize/decision_engine/consumer.py#L61-L65)).

---

## 2. At-least-once claim inventory (the claims to be MADE TRUE, not deleted)

**Completeness method.** Case-insensitive repo-wide search for the regex `at.least.once`
across all file types, then hand-classified each hit as *contract claim* (asserts the
guarantee) vs *supporting/idempotency* vs *historical session log*. The search is the
same one a reviewer can re-run: `rg -i "at.least.once"`. Ripgrep's `route.ts` blind spot
does not apply — no TypeScript file contains the phrase, and the website has **no bus
consumer** (§5). I assert this inventory is complete for the current tree at `244e2857`.

### 2a. Live contract claims (code + authoritative docs) — MUST be made/kept true

| # | Location | Claim |
|---|---|---|
| 1 | [`events/bus.py:68-69`](../../src/skylize/events/bus.py#L68-L69) | port invariant: "at-least-once delivery (consumers idempotent on `event_id`)" |
| 2 | [`events/redis_adapter.py:8`](../../src/skylize/events/redis_adapter.py#L8) | "At-least-once, via PEL reclaim." |
| 3 | [`events/memory_bus.py:14`](../../src/skylize/events/memory_bus.py#L14) | "honest model of at-least-once" |
| 4 | [`decision_engine/consumer.py:19`](../../src/skylize/decision_engine/consumer.py#L19) | "the router's retry/DLQ handling, which now does deliver at-least-once" |
| 5 | [`app/decision_engine/engine.py:14`](../../src/skylize/app/decision_engine/engine.py#L14) | "Delivery is at-least-once, so the engine is idempotent on `event_id`" |
| 6 | [`dal/decision_stores.py:16`](../../src/skylize/dal/decision_stores.py#L16) | "at-least-once redelivery racing a concurrent [write]" — `ON CONFLICT DO NOTHING` |
| 7 | [`docs/02_architecture/event_driven_architecture.md:28`](../../docs/02_architecture/event_driven_architecture.md#L28) | "at-least-once delivery + DLQ semantics" (§2) |
| 8 | [`docs/02_architecture/event_driven_architecture.md:305-306`](../../docs/02_architecture/event_driven_architecture.md#L305-L306) | "consumers must be idempotent on `event_id` (at-least-once …)" (§8) |
| 9 | [`docs/04_decision_engine/decision_flow.md:15`](../../docs/04_decision_engine/decision_flow.md#L15) | "effect despite at-least-once delivery" |
| 10 | [`docs/04_decision_engine/decision_flow.md:119`](../../docs/04_decision_engine/decision_flow.md#L119) | "**At-least-once** delivery (Redis Streams consumer group)" |
| 11 | [`docs/09_development/coding_standards.md:57`](../../docs/09_development/coding_standards.md#L57) | "Idempotent event consumers … at-least-once delivery" |
| 12 | [`docs/architecture/01_final_stack.md:120`](../../docs/architecture/01_final_stack.md#L120) | "consumer groups (at-least-once + DLQ)" |
| 13 | [`migrations/versions/0011_decision_engine_stores.py:13`](../../migrations/versions/0011_decision_engine_stores.py#L13) | "at-least-once redelivery [races safely]" |
| 14 | [`migrations/versions/0010_workflow_run_steps.py:31`](../../migrations/versions/0010_workflow_run_steps.py#L31) | "at-least-once activity delivery may append more than one row" (Temporal, not the bus — see note) |

Claim #14 is a **Temporal** activity-delivery statement, not an `EventBus` claim; it is
listed for completeness and is out of this audit's transport scope.

### 2b. Test assertions that pin the contract (must stay green)

| Location | Asserts |
|---|---|
| [`tests/integration/test_redis_bus.py:105-138`](../../tests/integration/test_redis_bus.py#L105-L138) | un-acked message IS redelivered by the adapter |
| [`tests/integration/test_redis_bus.py:198-243`](../../tests/integration/test_redis_bus.py#L198-L243) | one publish → N real redeliveries → DLQ (end-to-end at-least-once) |
| [`tests/integration/test_event_router.py:29-88`](../../tests/integration/test_event_router.py#L29-L88) | dispatch idempotent on `event_id`; budget counts real redeliveries |
| [`tests/integration/test_decision_engine_consumer_redis.py:304-349`](../../tests/integration/test_decision_engine_consumer_redis.py#L304-L349) | failed handler redelivered then DLQ'd; redelivered proposal decided once |
| [`tests/contract/test_tool_dedup_events.py:110-139`](../../tests/contract/test_tool_dedup_events.py#L110-L139) | idempotent consumer keyed on `event_id` processes a redelivery once |

### 2c. Historical / session-log references — NOT current contract (do not cite as truth)

| Location | Note |
|---|---|
| `OVERNIGHT_SESSION_2026-07-19.md:417,431` | records the *pre-reclaim* "at-MOST-once for failures" state — historical |
| `OVERNIGHT_SESSION_2026-07-21.md:46,108,129` | records reclaim landing; "at-least-once now holds" — session narrative |
| `OWNER_DECISIONS_QUEUE_2026-07-19.md:185,194` | the owner decision to add real at-least-once; "seven docstrings … corrected in `6cf271f2`" |
| [`docs/architecture/adr/0006-ai-cost-ledger.md:22`](../../docs/architecture/adr/0006-ai-cost-ledger.md#L22) | "Bus delivery semantics are under active repair; at-least-once/at-most-once is not currently a guarantee we can bill on." Still-Accepted ADR that **defers** to the settled contract ADR-0007 provides; not a contradiction (§7). |

---

## 3. Dead-config inventory — `decision_engine/config.py`

`DecisionEngineSettings` at [`config.py:8-24`](../../src/skylize/decision_engine/config.py#L8-L24).
"Read by live code?" is judged against production execution: the inline engine is the
only engine wired in production; the OPA worker is real but flag-gated off
([ADR-0004:41-46](../architecture/adr/0004-opa-production-arbiter.md#L41-L46), [`worker.py:20-27`](../../src/skylize/decision_engine/worker.py#L20-L27)).

| Setting | Declared | Read by | Verdict |
|---|---|---|---|
| `redis_idle_time_ms = 60000` | [`config.py:22`](../../src/skylize/decision_engine/config.py#L22) | **Yes** — `worker.py:161-164` passes it to `RedisEventBus(reclaim_min_idle_ms=…)`; asserted by [`test_decision_engine_wiring.py:77-86`](../../tests/integration/test_decision_engine_wiring.py#L77-L86). (Only in the flag-gated OPA worker.) | **WIRE — keep (already wired).** |
| `redis_max_retries = 3` | [`config.py:23`](../../src/skylize/decision_engine/config.py#L23) | **Yes** — `consumer.py:139` passes it to `EventRouter(dlq_after_retries=…)`. (Only in the OPA worker.) | **WIRE — keep (already wired).** |
| `redis_dlq_stream = "evt:dlq:decision_engine"` | [`config.py:21`](../../src/skylize/decision_engine/config.py#L21) | **No production reader.** Only the test fixture sets it (`tests/decision_engine/conftest.py:36`). The live DLQ is addressed by `dlq_name()` = `dlq:{tenant}:{department}`. Also a **second, conflicting** DLQ name (§4). | **DELETE.** |
| `redis_batch_size = 10` | [`config.py:24`](../../src/skylize/decision_engine/config.py#L24) | **No production reader.** The adapter hardcodes `XREADGROUP count=16` ([`redis_adapter.py:113`](../../src/skylize/events/redis_adapter.py#L113)) and has its own `reclaim_batch` (default 16, [`:47`](../../src/skylize/events/redis_adapter.py#L47)) which `worker.py` never sets. Only the test fixture reads it (`conftest.py:39`). | **DELETE** (or, if batch tuning is wanted, WIRE it to `RedisEventBus(reclaim_batch=…)` in `worker.py` — but do not leave a third, inert number). |

Live-path note: the **inline** engine's retry budget uses the *top-level*
`Settings.dlq_after_retries` ([`engine.py:122`](../../src/skylize/app/decision_engine/engine.py#L122)),
**not** `DecisionEngineSettings.redis_max_retries`. The two knobs govern different engines.

---

## 4. Two DLQ namings — canonical vs deprecated

| Naming | Source | Status |
|---|---|---|
| `dlq:{tenant}:{department}` | [`bus.py:32-34`](../../src/skylize/events/bus.py#L32-L34) `dlq_name()`; used by `redis_adapter.to_dlq`/`_raw_to_dlq` ([`:128`](../../src/skylize/events/redis_adapter.py#L128), [`:217`](../../src/skylize/events/redis_adapter.py#L217)), `memory_bus.to_dlq` ([`:88`](../../src/skylize/events/memory_bus.py#L88)); asserted by tests ([`test_redis_bus.py:98`](../../tests/integration/test_redis_bus.py#L98), [`:226`](../../tests/integration/test_redis_bus.py#L226)) | **CANONICAL** — matches arch doc §2 [`:40`](../../docs/02_architecture/event_driven_architecture.md#L40) and §9 [`:331`](../../docs/02_architecture/event_driven_architecture.md#L331) |
| `evt:dlq:decision_engine` | [`config.py:21`](../../src/skylize/decision_engine/config.py#L21) `redis_dlq_stream` | **DEPRECATED / DEAD** — a single global name, wrongly `evt:`-prefixed (that is the primary-stream namespace), never read in production |

**Canonical per the architecture doc:** `dlq:{tenant}:{department}` (§2, §9).
**What breaks if we unify (delete `redis_dlq_stream`):** nothing functional — no
production code path reads it; only the test fixture at `conftest.py:36` and the field
declaration reference it. The only real change is conceptual: DLQ triage tooling must
iterate DLQs **per (tenant, department)** rather than draining one global stream. Migration
path in ADR-0007 §"Canonical DLQ naming".

---

## 5. Consumer migration matrix

**Enumeration method.** `.consume(` has exactly one production caller —
`EventRouter.run` ([`router.py:67`](../../src/skylize/events/router.py#L67)); all other
matches are tests. `EventRouter` is used by exactly two consumers. The website was checked
by **direct file read** (not grep) per the `route.ts` caveat: [`workflows/route.ts`](../../website/src/app/api/console/workflows/route.ts)
and siblings are n8n **HTTP** proxies, not Redis-Streams consumers; a content search for
`consume`/`XREADGROUP`/`EventBus` under `website/` returns nothing. There is **no
TypeScript bus consumer**.

| Consumer | Live? | Ack behaviour today | Idempotency today | Required change |
|---|---|---|---|---|
| Inline `DecisionEngine` (`app/decision_engine/engine.py`) | **Yes** (production default) | ack-after-success via `EventRouter` ([`router.py:86`](../../src/skylize/events/router.py#L86)); subscribes per (tenant, dept) with `cg:decision_engine` ([`engine.py:116-121`](../../src/skylize/app/decision_engine/engine.py#L116-L121)) | `ProcessedEventStore` on `event_id` ([`engine.py:154`](../../src/skylize/app/decision_engine/engine.py#L154)) + deterministic `decision_id` → `ON CONFLICT` ([`engine.py:23-30`](../../src/skylize/app/decision_engine/engine.py#L23-L30)) | **None for delivery conformance.** Already conforms. Emit-before-mark window ([`engine.py:158-159`](../../src/skylize/app/decision_engine/engine.py#L158-L159)) is benign via `ON CONFLICT`. |
| OPA `DecisionEngineConsumer` (`decision_engine/consumer.py`) | **No** — flag-gated, fail-closed ([ADR-0004:41-46](../architecture/adr/0004-opa-production-arbiter.md#L41-L46), [`worker.py:70-83`](../../src/skylize/decision_engine/worker.py#L70-L83)) | ack-after-success via `EventRouter`; consumer host-qualified ([`consumer.py:138`](../../src/skylize/decision_engine/consumer.py#L138)); budget `redis_max_retries` ([`consumer.py:139`](../../src/skylize/decision_engine/consumer.py#L139)) | Durable `PgProcessedEventStore` on `event_id` ([`consumer.py:222`](../../src/skylize/decision_engine/consumer.py#L222), [`worker.py:130-136`](../../src/skylize/decision_engine/worker.py#L130-L136)) + `resume_dedup_key(hitl_id)` for verdicts ([`consumer.py:284`](../../src/skylize/decision_engine/consumer.py#L284)) | **None for delivery conformance.** Stays behind the flag per ADR-0004; unblocking is ADR-0004's transport/policy/parity work, not this ADR. |
| `EventRouter` (shared mechanism, not itself a consumer) | n/a | owns ack/retry/DLQ ([`router.py:76-100`](../../src/skylize/events/router.py#L76-L100)) | in-process `_seen` set ([`router.py:54`](../../src/skylize/events/router.py#L54)) — a *second* idempotency layer, non-durable | Residual: process-memory retry budget (§0, §1). **Out of scope for ADR-0007** (needs a port change — durable delivery count). |
| `RedisGovernanceBroadcast` (Pub/Sub kill-switch) | Yes | **Not the EventBus.** Fire-and-forget Pub/Sub, intentionally bypasses DLQ/retry/ordering ([`app/governance/broadcast.py:12`](../../src/skylize/app/governance/broadcast.py#L12)) | invalidation is idempotent by construction | **None.** Explicitly out of scope (different transport). Listed so it is not mistaken for a missed consumer. |

**Migration-risk answer (THINK):** if `consume()` "starts requiring explicit
ack-after-success," **zero** existing consumers break — the adapter already never
auto-acks live messages ([`redis_adapter.py:124-125`](../../src/skylize/events/redis_adapter.py#L124-L125)),
and the only production consumer path (`EventRouter`) already acks strictly after handler
success ([`router.py:81-86`](../../src/skylize/events/router.py#L81-L86)). ADR-0007
**codifies existing behaviour**; it does not force a migration.

---

## 6. Reclaim vs per-`partition_key` ordering (the hardest question)

**Does per-`partition_key` ordering survive a reclaim? No.** This holds even with a
**single** worker, so it is not merely a horizontal-scaling concern.

Mechanism, from code:

1. Consumer `c` reads `E1` (`partition_key=p`, stream id `5`) via `">"`. Its handler
   raises; `EventRouter` does **not** ack ([`router.py:97-100`](../../src/skylize/events/router.py#L97-L100)),
   so `E1` sits in the PEL with its idle clock at 0.
2. The next `consume` pass runs reclaim first, but `E1` is idle `< reclaim_min_idle_ms`
   (60s) so it is **not** yet reclaimed ([`redis_adapter.py:90-92`](../../src/skylize/events/redis_adapter.py#L90-L92),
   [`:152-159`](../../src/skylize/events/redis_adapter.py#L152-L159)). The `">"` read then
   returns `E2` (`partition_key=p`, stream id `8`, never-delivered) — `XREADGROUP ">"`
   delivers new entries regardless of un-acked earlier ones
   ([`redis_adapter.py:109-113`](../../src/skylize/events/redis_adapter.py#L109-L113)).
3. `E2` is processed and acked. ~60s later `E1` is reclaimed and reprocessed
   ([`redis_adapter.py:135-177`](../../src/skylize/events/redis_adapter.py#L135-L177)).

Net: on the same `partition_key`, `E2` (later id) is decided **before** `E1` (earlier id) —
a strict inversion of the order the doc guarantees
([`event_driven_architecture.md:294`](../../docs/02_architecture/event_driven_architecture.md#L294),
invariant #4 [`:407-408`](../../docs/02_architecture/event_driven_architecture.md#L407-L408)).
The doc's stated defence — *"we additionally pin a `partition_key` to a consumer via
consistent hashing"* ([`:301-304`](../../docs/02_architecture/event_driven_architecture.md#L301-L304)) —
**does not exist in code**: a search for hashing/pinning across `src/skylize/events/`
returns nothing, and consumers are named per (tenant, department) only
([`consumer.py:138`](../../src/skylize/decision_engine/consumer.py#L138),
[`engine.py:121`](../../src/skylize/app/decision_engine/engine.py#L121)). Even a pin would
not fix intra-consumer reordering; it only addresses cross-consumer races.

**Consequence — decision engine.** A budget-reallocation proposal on
`partition_key=campaign:42` that fails transiently is decided ~60s after a *later*
proposal on the same campaign has already committed a spend decision. Capital-allocation
outcomes on one campaign can therefore be applied out of causal order. Idempotency does
**not** save this: `ProcessedEventStore` and the deterministic `decision_id`/`ON CONFLICT`
([`engine.py:23-30`](../../src/skylize/app/decision_engine/engine.py#L23-L30),
[`orchestrator.py:19-24`](../../src/skylize/decision_engine/orchestrator.py#L19-L24))
prevent **duplicate** decisions, not **misordered** ones.

**Consequence — HITL queue.** A `decision.deferred_to_human` for the reclaimed `E1` can be
enqueued after a human has already acted on `E2`'s decision for the same partition. The
deterministic `hitl_id` ([`consumer.py:266`](../../src/skylize/decision_engine/consumer.py#L266),
[`resume.py:83-91`](../../src/skylize/decision_engine/resume.py#L83-L91)) keeps a redelivered
verdict idempotent, but does not impose an order between the two decisions a human sees.

**Mitigation (specified normatively in ADR-0007).** Redis Streams consumer groups do not
offer per-partition head-of-line blocking, so strict order under failure cannot be had
from the transport alone. The realistic mitigations, in preference order:

1. **Monotonic partition guard at the decision sink** (recommended): the publisher/sink
   rejects (or no-ops) applying a decision whose source event is *older* on its
   `partition_key` than the last-applied one — turning "strict order" into "no stale
   overwrite," which is what capital allocation actually needs. Builds on the existing
   `ON CONFLICT` posture rather than fighting the transport.
2. **Document the guarantee honestly** as *per-`partition_key` order in the absence of
   handler failure; on failure, order is eventually-consistent with a no-stale-overwrite
   guard* — and correct arch doc §8/§13 and delete the unimplemented consistent-hash claim.
3. (Rejected as over-engineering for MVP) a partition-scoped in-flight lock that blocks
   `">"` delivery of `key=p` while an earlier `key=p` entry is un-acked.

ADR-0007 adopts (1)+(2) and records (3) as out of scope.

---

## 7. Coded-but-never-executed (implemented, no live execution path)

| Item | Evidence | Note |
|---|---|---|
| Entire OPA consumer/worker path (`decision_engine/consumer.py`, `worker.py`) | fail-closed on `SKYLIZE_DECISION_ENGINE=opa` ([`worker.py:20-27`](../../src/skylize/decision_engine/worker.py#L20-L27), [`:70-83`](../../src/skylize/decision_engine/worker.py#L70-L83)); flag pinned `"inline"` ([ADR-0004:41-46](../architecture/adr/0004-opa-production-arbiter.md#L41-L46), [ADR-0005:3](../architecture/adr/0005-decision-engine-department-vocabulary.md#L3)) | Runnable and tested, but not reachable from default config. This is the reference at-least-once implementation the audit brief points to — coded, not live. |
| `redelivery_count` (wire field) | [`base.py:69-70`](../../src/skylize/schemas/base.py#L69-L70) — never written anywhere | Arch doc §3/§9 assume the bus sets/increments it; it is inert. The real counter is `router._attempts` in process memory. |
| Consistent-hash partition pin | arch doc §8 [`:301-304`](../../docs/02_architecture/event_driven_architecture.md#L301-L304) — no code | Documented ordering defence that does not exist (§6). |
| `redis_dlq_stream`, `redis_batch_size` | [`config.py:21`](../../src/skylize/decision_engine/config.py#L21), [`:24`](../../src/skylize/decision_engine/config.py#L24) | Dead config (§3); read only by test fixtures. |
| `redis_idle_time_ms`, `redis_max_retries` | wired only into the (dormant) OPA worker ([`worker.py:163`](../../src/skylize/decision_engine/worker.py#L163), [`consumer.py:139`](../../src/skylize/decision_engine/consumer.py#L139)) | Wired but dormant — real once the flag flips. |

---

## 8. ADR-0004 blocker classification (mandated)

ADR-0004's three pre-cutover blockers ([`:41-46`](../architecture/adr/0004-opa-production-arbiter.md#L41-L46)):

| Blocker | Delivery-semantics blocker? | Reasoning |
|---|---|---|
| (a) Transport rebuild onto the `EventBus` port ([`:42`](../architecture/adr/0004-opa-production-arbiter.md#L42)) | **Yes** — the only one | Its correctness *is* the delivery contract: the rebuilt consumer "now does deliver at-least-once" only because the shared adapter reclaims ([`consumer.py:19-24`](../../src/skylize/decision_engine/consumer.py#L19-L24)). Cannot be cleared until the bus provides at-least-once with idempotent consumers — which it now does. Effectively **cleared**, but its clearance *depends on* the contract ADR-0007 codifies. |
| (b) Rego policy set + `policy_inputs.md` ([`:43`](../architecture/adr/0004-opa-production-arbiter.md#L43)) | No | Policy authoring; independent of transport. |
| (c) Wire-level `decision.*` payload parity ([`:44`](../architecture/adr/0004-opa-production-arbiter.md#L44)) | No (delivery-adjacent) | About payload shape, not delivery. It *assumes* both engines emit the same events; if delivery silently dropped them the parity check would be moot, but the blocker as written is not a delivery blocker. |

**Does ADR-0004 assume a delivery guarantee today's bus does not satisfy?** Yes — see §0.
The at-least-once precondition is met; the **per-`partition_key` ordering** the six-stage
model implicitly relies on is not (§6). That is the single most important finding.

**Does ADR-0007's proposed contract contradict ADR-0004 or ADR-0005?** No.
- ADR-0004: the seam stays a plain `EventBus` consumer/producer ([`:52`](../architecture/adr/0004-opa-production-arbiter.md#L52)); ADR-0007 codifies that seam's semantics. No contradiction. (ADR-0007 does **not** flip `SKYLIZE_DECISION_ENGINE`, does not touch the OPA-vs-inline arbiter question — that stays ADR-0004's, unresolved parts and all.)
- ADR-0005: department-vocabulary; orthogonal to delivery. No contradiction; ADR-0005 is untouched.
- ADR-0006: states bus semantics are "under active repair" and declines to bill on them ([`:22`](../architecture/adr/0006-ai-cost-ledger.md#L22)). ADR-0007 *settles* the contract ADR-0006 deferred; complementary, not contradictory.

No `STOP_ON_ARCHITECTURE_CONFLICT` against any ADR. The one conflict found is
**doc-vs-code** (arch doc §8/§13 ordering claims vs the reclaim path), reported in §0/§6
and recorded — not silently resolved — in ADR-0007.

---

## 9. Test baseline

Owner-provided measured baseline: **1086 passed / 31 skipped**. This audit did not run the
full suite (it needs Redis + Postgres service containers; the 31 skips are the
service-gated integration tests, e.g. `requires_redis` at
[`test_redis_bus.py:32`](../../tests/integration/test_redis_bus.py#L32)). Corroboration:
`pytest --co -q` collected **1136 tests** at `244e2857`, consistent with the baseline.
