# Audit: The Decision-Consumer Gap and Sink Convergence

**Date:** 2026-07-25
**Author:** Principal Architect terminal (read-only audit)
**Branch / base:** `audit/decision-consumer-gap`, cut from `feat/durable-governance` @ `fb47e103`
**Scope:** `evt:{tenant}:decision` has no consumer. What does building one actually require, and can the two decision engines share one write path?
**Test baseline (measured, this commit):** `python -m pytest -q` → **1104 passed, 38 skipped, 0 failed** (1142 collected), 62.9 s.

Every claim below carries `file:line` against `fb47e103`. Anything not directly verified is marked **UNVERIFIED**.

---

## 1. Premise verification

The brief carried four stated premises and one embedded one. Two are wrong in ways that change the design.

### P1 — "`evt:{tenant}:decision` has zero subscribers anywhere in src/skylize/**"

**CONFIRMED — and understated.**

- Stream keys are built only by `stream_name()` (`src/skylize/events/bus.py:27-29`); consumption goes only through `EventBus.consume` → `RedisEventBus.consume` (`src/skylize/events/redis_adapter.py:79-122`), driven by `EventRouter.run` (`src/skylize/events/router.py:64-74`).
- There are exactly two `EventRouter(` construction sites in `src/skylize/**`: the inline engine (`src/skylize/app/decision_engine/engine.py:118`) and the OPA consumer (`src/skylize/decision_engine/consumer.py:133`).
- The inline engine subscribes `DEFAULT_DEPARTMENTS = ("creative", "growth", "governance")` (`engine.py:62`). The OPA consumer subscribes `SUBSCRIBED_DEPARTMENTS` (`consumer.py:83,108`), which is the key-set of `ALLOWED_EVENT_TYPES_BY_DEPARTMENT` — `creative`, `growth`, `governance` (`src/skylize/decision_engine/constants.py:28-44,80`). Neither set contains `decision`.
- The only raw `xreadgroup` in the tree is inside the adapter itself (`redis_adapter.py:111`). No other module reads any stream.

**The understatement:** the gap is not an OPA problem. The *inline* engine — the one running in production — publishes six event types onto `evt:{tenant}:decision` (`department="decision"` at `engine.py:168,187,201,217,237,255,295,311`) and nothing consumes them either. Every terminal decision the live system has ever emitted has gone onto a stream nobody reads.

### P2 — "The OPA engine emits decisions via outbox → that stream and does nothing else with them"

**PARTIALLY FALSE.** The emission is outbox-only — `DecisionEventPublisher` never touches Redis (`src/skylize/decision_engine/publisher.py:1-17,226-234`); the `OutboxPoller` relays (`src/skylize/decision_engine/outbox_poller.py:139,161`). But "does nothing else" is wrong: the OPA path also durably writes the `decisions` row (same statement as the outbox row, `publisher.py:272-311`), the `hitl_queue` row plus a raw governance-stream XADD on a defer (`src/skylize/decision_engine/orchestrator.py:84-100`, `src/skylize/decision_engine/hitl_writer.py:133-173,195-227`), a per-stage audit mirror onto `evt:{tenant}:audit` (`src/skylize/decision_engine/pipeline.py:512-558`), and a durable processed marker (`consumer.py:245-247`, `src/skylize/dal/decision_stores.py:86-97`). The OPA engine's own sinks are exactly the sinks a decision consumer would have needed to duplicate.

### P3 — "The inline engine reaches its outcomes by direct in-process writes rather than by consuming that stream"

**HALF TRUE, and the false half matters.** True: inline never consumes the decision stream. False: its terminal outcome is not a "direct write" — it is a bus *publish onto that same unconsumed stream* (`engine.py:212-266`). The inline engine's only direct durable writes on an outcome are the audit pair (bus event + `audit_log` append via `AuditService.record`, `engine.py:268-281`, `src/skylize/app/audit/service.py:78-95`) and the processed marker (`engine.py:159`).

Two sinks the brief assumed exist **do not exist on the inline path at all**:

- **Capital reservation: nonexistent on both paths.** The inline evaluator only *reads* the ceiling (`get_ceiling`, `src/skylize/app/decision_engine/evaluator.py:131`); the OPA `CapitalDAL` is likewise read-only (`src/skylize/decision_engine/capital_dal.py:38-132` — SELECTs only). `budget_ledger.committed` is never incremented by any decision anywhere; the only writer is `PgCapitalRepository.set_ceiling`, an explicit seeding/ops helper "not on the port — the evaluator only reads" (`src/skylize/dal/decision_stores.py:55-72`). An approved spend today commits no budget. The "capital reservation" terminal write the brief asked me to inventory is a phantom.
- **HITL row: OPA-only.** `hitl_queue` is written exclusively by `HITLQueueWriter` (`hitl_writer.py:136`) and updated exclusively by `HITLResumeHandler` (`src/skylize/decision_engine/resume.py:117`) — both OPA-engine modules, neither wired into the running API process (`src/skylize/bootstrap.py:236-257` wires inline only). An inline `deferred_to_human` produces an event on an unconsumed stream plus an audit row, and **no durable work item any human surface could serve**. No API route reads `hitl_queue` (grep over `src/skylize/app/**`: no hits outside `app/decision_engine`), and nothing in `src/` publishes `GovernanceHumanApprovalReceived` (only the schema at `src/skylize/schemas/events/governance.py:142`) — the resume entrypoint has no in-process producer either.

### P4 — ADR-0004 status and blocker list

**CONFIRMED.** `docs/architecture/adr/0004-opa-production-arbiter.md` — Status: Accepted (line 3); OPA is "Skylize's designated production governance arbiter for MVP launch" (line 31); the flag is `Literal["inline", "opa"] = "inline"` and both sides fail closed on it (`src/skylize/config.py:107`, `bootstrap.py:251-257`, `src/skylize/decision_engine/worker.py:70-83`).

The blocker list, verbatim (ADR-0004, lines 41-46):

> **4. `"opa"` is not yet enablable in production, and this ADR does not make it so.** Selecting OPA as the production arbiter is the *destination* this ADR commits to, not a claim that the cutover is complete today. Before any environment may set `SKYLIZE_DECISION_ENGINE=opa`:
> - the OPA consumer transport (`decision_engine/consumer.py`, `constants.py`) must be rebuilt onto the live `EventBus` port — the same seam the inline engine already uses — replacing the current placeholder Redis-stream event types with real, schema-backed ones;
> - the Rego policy set the OPA engine evaluates against must be defined and reviewed against a `policy_inputs.md` (or equivalently named) input-contract document, which does not yet exist and must be authored before policies are written against assumed inputs;
> - wire-level parity between the two engines' `decision.*` event payloads must be confirmed, so that swapping the arbiter does not silently change what downstream consumers (audit, HITL, capital allocation) receive.
>
>    Until all three land, `SKYLIZE_DECISION_ENGINE` must fail closed to `"inline"` being the only accepted value, exactly as prototyped on `feat/opa-composition-glue`.

And the Consequences restatement (lines 64-65):

> - **Transport rebuild is required before OPA can run anywhere**, tracked as launch-blocking follow-up: rebuild `decision_engine/consumer.py` / `constants.py` onto the live `EventBus`, replacing placeholder Redis-stream event types with real ones.
> - **Rego policy authoring is gated on a `policy_inputs.md` input contract** that does not yet exist and must be authored before production Rego policies are written or reviewed.

### P5 (embedded) — "hitl_id minting is a known open issue — the writer generates its own uuid4"

**STALE — already fixed.** Commit `99ad381f` ("mint hitl_id once (deterministic uuid5), flow through event + queue row") landed before this audit. Today the orchestrator mints `hitl_id = hitl_id_for(result.decision_id)` once, upstream of both writers (`orchestrator.py:74-83`), deterministically (uuid5, `pipeline.py:75-83`); the publisher *requires* it for a deferral payload (`publisher.py:461-465`) and the writer receives it as a parameter (`hitl_writer.py:97-118`). The writer's only remaining `uuid4()` is a correlation-id fallback (`hitl_writer.py:127-131`), not the ticket id. **A decision consumer can therefore produce a correct HITL row without a minting fix — the fix already landed.** What is *not* fixed is cross-engine id parity — see §4.

---

## 2. Sink inventory

Every terminal write performed when either engine reaches an outcome. "Reusable" = callable, as-is, by something that is not that engine.

| # | Sink | Module:line | Engine | Reusable? | Required context | Blockers for a stream consumer |
|---|------|-------------|--------|-----------|------------------|-------------------------------|
| 1 | Terminal `decision.*` events → `evt:{tenant}:decision` | inline: `app/decision_engine/engine.py:162-266` via `EventBus.publish`; OPA: `publisher.py:272-311` → `outbox_poller.py:139` | both | yes (bus port) / yes (outbox) | full `BaseEvent` envelope | This IS the unconsumed stream — a consumer reads it, it doesn't write it |
| 2 | `decisions` row (durable decision record) | `publisher.py:276-284` (`INSERT … ON CONFLICT (decision_id) DO NOTHING`) | OPA only | **no** — `publish_outcome(result: DecisionResult, hitl_id)` takes OPA's engine-specific `DecisionResult` (`decision_engine/models.py:77-90`) and scavenges context out of `step.detail` via `_extract_*` helpers (`publisher.py:113-187`) with lossy fallbacks (`uuid4()` correlation, `"unknown"` agent/action) | tenant_id, decision_id, correlation/causation, partition_key, proposing_agent, authority_level, action_kind, proposal_json, outcome, score_json, governance_token_id | Inline writes no `decisions` row at all; event payloads don't carry proposing_agent/authority/score in reusable form (§3) |
| 3 | `hitl_queue` row (deferral ticket) | `hitl_writer.py:133-153`; called from `orchestrator.py:84-100` | OPA only | **no** — takes `DecisionContext` + `DecisionResult` (OPA types); `proposal_json` embeds the full original event payload and all six step records (`hitl_writer.py:38-58`) | hitl_id (minted upstream), decision_id, tenant, event_id (as partition_key for dedup, `hitl_writer.py:146`), full original payload, evaluation steps, score, trigger reason, expiry | `decision.deferred_to_human` payload carries none of the evidence the row needs (§3); inline path never writes this row |
| 4 | Governance escalation XADD → `evt:{tenant}:governance` | `hitl_writer.py:195-227` (raw `xadd`, flat fields) | OPA only | no — private method, raw Redis client | hitl_id, decision_id, reason, risk_score | Wire format is not bus-decodable (§5, W2) |
| 5 | Audit record (bus event + `audit_log` append) | inline: `engine.py:268-281` → `app/audit/service.py:41-96`; OPA: per-stage `pipeline.py:512-558` (bus only, fire-and-forget) | both, differently | **yes** — `AuditService.record` is engine-neutral, keyed on scalars (`service.py:41-56`) | org_id, correlation_id, action_type, result, + optional agent/authority/token/causation/hashes | The closest thing to a shared sink that exists today |
| 6 | Processed marker (idempotency) | inline: `engine.py:159`; OPA: `consumer.py:245-247`; durable impl `dal/decision_stores.py:86-97` (`ON CONFLICT DO NOTHING`) | both | yes (port) | key (event_id or `hitl:{hitl_id}`), outcome, org_id | none — already shared via the `ProcessedEventStore` port |
| 7 | Resume transition (hitl_queue UPDATE + decisions UPDATE + outbox INSERT, one tx) | `resume.py:113-156` | OPA only | no — takes the typed verdict event, but is the right *shape* for reuse | hitl_id, tenant, verdict, decision_id | Single-transaction CTE chain; cannot be split across a stream hop (§5) |
| 8 | Capital reservation | **does not exist** | neither | — | — | `budget_ledger.committed` has no decision-path writer (`capital_dal.py` is SELECT-only; `decision_stores.py:55-56` is a seeding helper). Building it is new work, not refactor |
| 9 | `mirror_audit_step` (`audit_log` insert per stage) | `publisher.py:325-379` | OPA | n/a | — | **Dead code**: no caller in `src/` (grep: only its own definition + docstring; exercised only by `tests/decision_engine/test_publisher.py`) |

---

## 3. Decision-event payload vs. what the sinks need

The wire payloads (`src/skylize/schemas/events/decision.py`) against the sink columns:

- `DecisionApproved.Payload`: `decision_id, action_kind, approved_scope: dict[str,str]` (`decision.py:38-43`).
- `DecisionRejected.Payload`: `decision_id, action_kind, stage_rejected_at, reasons, policy_version` (`decision.py:52-58`).
- `DecisionDeferredToHuman.Payload`: `decision_id, hitl_id, trigger_reason, routed_to` (`decision.py:68-74`).
- The `BaseEvent` envelope adds `tenant_id, partition_key, department, source_agent_id, authority_level, governance_token_id, causation_id, correlation_id, occurred_at` (`src/skylize/schemas/base.py:49-70`) — but the OPA publisher populates only tenant/partition/department/correlation on its outbound events (`publisher.py:430-478`; no `source_agent_id`, no `authority_level`, no `governance_token_id`, no `causation_id` on the typed constructors), while the inline engine sets `governance_token_id` and `causation_id` but not `source_agent_id`/`authority_level` (`engine.py:214-231`).

What a consumer of these events **cannot** reconstruct, per sink:

| Sink need | On the event? |
|---|---|
| `decisions.proposing_agent`, `authority_level` | No (inline smuggles agent inside `approved_scope["agent"]` on approvals only, `engine.py:225`; OPA doesn't) |
| `decisions.proposal_json` (full evidence), `score_json` | No — no event carries the step records or scores; `decision.evaluated` (inline-only, `engine.py:164-180`) carries stage *names*, not outcomes/details, and the OPA engine never emits `decision.evaluated` at all (`publisher.py:51-56` maps three outcomes + escalation only) |
| `hitl_queue.proposal_json` (original business payload + steps), `expires_at`, `score_json` | No — `deferred_to_human` carries four scalar fields |
| Capital: spend amount, currency, capital scope | No decision event carries spend at all |
| Staleness key (inbound proposal stream entry id) | No — and it isn't even *available* to be put there: handlers receive only `BaseEvent`; `DeliveredEvent.message_id` dies at the router (`router.py:37`, `router.py:83`) |

**Conclusion:** the current decision events are notifications, not state-transfer. A consumer that must produce today's `decisions`/`hitl_queue` rows from them cannot; either the payloads grow (schema_version bump) or those rows stay written by the engines and the consumer serves only *downstream* effects (capital, notifications, projections).

---

## 4. Wire-format and identity parity (ADR-0004 blocker c, measured)

Three concrete parity breaks exist today — all three would bite the moment anything consumed the stream:

**W1 — Envelope mismatch on `evt:{tenant}:decision`.** The inline engine publishes through `RedisEventBus.publish`, one `event` field holding the full JSON (`redis_adapter.py:41,65-69`). The OutboxPoller relays OPA rows as *flattened dot-key fields* (`outbox_poller.py:130-139`, `_flatten_for_stream` at `230-242`). `RedisEventBus._decode` reads only the `event` field and returns `None` otherwise (`redis_adapter.py:197-209`), and `_admit` acks-and-DLQs undecodable entries (`redis_adapter.py:188-194`). **Any bus-port consumer of the decision stream would process every inline event and route every OPA event to the DLQ as `schema_rejected`.** The consumer cannot be built until one side changes.

**W2 — Same mismatch on `evt:{tenant}:governance`.** `HITLQueueWriter._emit_governance_event` XADDs flat fields with no `event` envelope (`hitl_writer.py:204-215`). Both engines' consumers *do* subscribe to governance (`engine.py:62`; `constants.py:43,80`). If the OPA worker ran alongside anything consuming governance through the bus port, every escalation event it emits would be DLQ'd on arrival. Latent today (the OPA worker doesn't run); fatal at cutover.

**W3 — Identity divergence.** For the same source event the engines mint different ids. Inline: `decision_id = uuid5(6f9619ff-…, "decision:{proposal_id}")`, `hitl_id = uuid5(6f9619ff-…, "hitl:{proposal_id}")` (`app/decision_engine/events.py:33,49-56`). OPA: `decision_id = uuid5(uuid5(NAMESPACE_URL, "skylize.decision_engine.decision_id"), event_id)`, `hitl_id = uuid5(uuid5(NAMESPACE_URL, "skylize.decision_engine.hitl_id"), decision_id)` (`pipeline.py:63-83`). Both are deterministic — but mutually incompatible. A cutover mid-flight would re-decide in-flight proposals under new ids, and any store keyed on decision_id splits.

---

## 5. Transaction boundaries: what the OPA path relies on, and what a post-ack consumer loses

The brief asked what atomicity "the inline engine" relies on. The honest, measured answer inverts that: **the inline path has no Postgres transaction at all on an outcome** — it performs a sequence of independent bus publishes then two non-atomic audit writes (`engine.py:162-281`; `service.py:78-95` — the bus publish and the `audit_log` append are two awaits, no tx). Its safety comes from deterministic ids plus emit-before-mark ordering (`engine.py:23-30,158-159`).

The transactional structure worth preserving is on the **OPA** path:

- **T1 (atomic):** `decisions` + `decision_outbox` in one statement inside one `tenant_session` transaction (`publisher.py:272-311`; `tenant_session` wraps in `conn.transaction()`, `src/skylize/dal/connection.py:38-48`). This is the no-dual-write guarantee.
- **T2 (separate tx):** `hitl_queue` INSERT (`hitl_writer.py:133-153`) commits after T1. The crash window between them is **self-healing only via redelivery**: the processed marker is written last (`consumer.py:243-247`), so a crash re-delivers the proposal, the pipeline recomputes the same `decision_id`, T1 no-ops on `ON CONFLICT`, and `check_duplicate_escalation` finds no pending row → the ticket gets written (`orchestrator.py:86-100`).
- **T3 (atomic):** resume is one CTE chain — `hitl_queue` UPDATE gated on `status='pending'` → `decisions` UPDATE → outbox INSERT (`resume.py:113-156`). Idempotency and atomicity in a single statement.

**What breaks for a stream consumer.** All three properties are anchored on *redelivery-until-durably-recorded*: an exception propagates, the message stays in the PEL, XAUTOCLAIM re-delivers (`redis_adapter.py:8-14,86-97`). A consumer of `evt:{tenant}:decision` that acks and *then* writes sinks has inverted this: after the ack there is no redelivery, so every partial-failure window (wrote capital, crashed before HITL row; wrote nothing, crashed after ack) is **permanent silent loss**. State these as risks:

- **R1:** A decision consumer must write its sinks *before* acking, and each write must be idempotent under redelivery (the same discipline `_handle_event` already follows). Any design that acks first is unsound regardless of dedupe.
- **R2:** Multi-sink writes from a consumer are not one transaction unless they share one `tenant_session`. Writes that today ride T1's single statement (decision + outbox) cannot be split across a stream hop and re-joined; if the consumer owns some sinks and the engine owns others, the cross-sink invariant ("no event without its row") must be re-derived per sink pair, not assumed.
- **R3:** The router's retry budget is per-process memory (`router.py:14-23,55`); a consumer that crashes its worker on a poison decision event retries forever rather than converging on the DLQ. Known, documented, unfixed (`consumer.py:61-65`).

---

## 6. Idempotency vs. ordering — these are different problems

Kept strictly separate, as they must be in any implementation and its tests:

- **Idempotency (solved, twice):** the *same* event delivered twice. Solved by `event_id` dedupe — `ProcessedEventStore` first-write-wins (`decision_stores.py:86-97`), plus deterministic `decision_id` collapsing on `ON CONFLICT` (`publisher.py:250-253,282`). A decision consumer gets this for free by keying its own processed store on the decision event's `event_id`.
- **Ordering (unsolved):** two *different* events on one `partition_key` arriving out of order — concretely, a reclaim: event A (older) sits un-acked in the PEL while event B (newer) is processed from the `">"` read; A is later reclaimed and processed, overwriting B's effect with staler state. `event_id` dedupe is *silent* on this — A was genuinely never processed. No guard exists anywhere in the tree today. A test suite that replays the same event twice proves idempotency and proves **nothing** about this; the test that matters delivers two distinct events on one partition key in reversed order and asserts the older one cannot overwrite.

### Guard placement recommendation

The staleness key is the **inbound proposal stream entry id** (`DeliveredEvent.message_id`, monotonic within a stream — `bus.py:52`). Findings and recommendation:

1. **The key currently reaches no one.** Handlers are `Callable[[BaseEvent], Awaitable[None]]` (`router.py:37`); `message_id` is dropped at dispatch (`router.py:83`). Step zero on *both* paths is a router-level change (widen the handler contract to receive `DeliveredEvent`, or a parallel `on_delivered` registration) — one shared-transport change, one implementation, both engines inherit it.
2. **The guard belongs in the durable decision sink** — a `source_entry_id` column on `decisions`, with the terminal write gated `WHERE source_entry_id IS NULL OR source_entry_id < $new` (compare as Redis ids, ms-then-seq). That is the single point both engines' state converges on **after** the shared-sink refactor. Key propagation per path:
   - **OPA path:** `DeliveredEvent.message_id` → new field on `DecisionContext` (`models.py:67-74`) → carried on `DecisionResult` or passed alongside it → `publish_outcome` writes the column. Pure threading; no schema change to events needed for the engine's own write.
   - **Inline path:** `DeliveredEvent.message_id` → new field on `DecisionProposal` (`app/decision_engine/events.py:93-122`) → the shared sink write. **Caveat, stated plainly:** the inline engine writes no `decisions` row today (§2 row 2), so there is nothing on the live inline path for this guard to protect *until* inline adopts the shared decision-persistence sink. "ONE implementation covering the live inline path today" is achievable only as part of the convergence in §7 — a guard shipped before the shared sink would, on the inline path, guard a write that doesn't happen.
3. **Yes, it must also become a field on the decision event** — but only for the *consumer's* downstream writes. A consumer of `evt:{tenant}:decision` guarding its own per-partition state (capital, projections) needs the staleness key on the payload it receives; it cannot see the proposal stream's entry ids otherwise. (The decision event's *own* entry id is not a substitute: it orders by outbox-relay commit time, not proposal arrival, and the reclaim-reorder the guard exists for happens on the consumer's side of the decision stream too.) This is a `schema_version` bump on the terminal `decision.*` payloads.

---

## 7. Central verdict: can both engines write through one shared sink layer?

**Yes-with-refactor.** The sinks themselves are small and the seams mostly exist (two are already shared ports); what blocks convergence is not architecture but three specific couplings. What must move:

1. **The publisher's input type** (the largest piece). `publish_outcome` takes OPA's `DecisionResult` and scavenges eight context fields out of free-form `step.detail` dicts with lossy fallbacks (`publisher.py:113-187` — `uuid4()` when correlation is absent, `"unknown"` agent, `"worker"` authority). Replace with an engine-neutral outcome record carrying explicit fields (tenant, decision_id, correlation/causation, partition_key, proposing_agent, authority_level, action_kind, outcome, reasons, score, spend, governance_token_id, source_entry_id). Both engines can already populate it: inline from `DecisionProposal`+`DecisionResult` (`app/decision_engine/events.py:93-122,219-237`), OPA from `DecisionContext`+its `DecisionResult`. The `_extract_*` helpers are deleted, not adapted — they are the measure of the missing context, and they go away when the context arrives explicitly.
2. **Wire-format unification** (W1/W2, §4): the OutboxPoller and `_emit_governance_event` must emit the bus envelope (`{event: <json>}`) instead of flattened fields, or no bus-port consumer can ever read them. This is a relay-side change; the outbox rows already store the full validated event JSON (`publisher.py:309`).
3. **Identity unification** (W3, §4): one `decision_id`/`hitl_id` derivation, one namespace, both engines. Mechanical, but it invalidates ids in any existing dev data and must land before any store keys on them in production.
4. **`HITLQueueWriter` decoupling** (smaller): its inputs are OPA types but its *needs* are generic (§2 row 3); the same neutral outcome record plus the original event payload covers it. The inline path then gains the durable HITL ticket it currently lacks entirely.

What does **not** move: `AuditService` (already neutral), `ProcessedEventStore` (already a shared port), the outbox pattern itself, T3's resume CTE.

**And, separately: the "decision consumer" should not be the thing that writes `decisions`/`hitl_queue`.** The engines' own sinks are transactional with evaluation (T1) or redelivery-healed against it (T2); pushing them across a stream hop forfeits that for nothing (§5). The consumer that is actually missing is a *downstream effects* consumer — capital commitment (a sink that does not exist yet, §2 row 8), notification/projection fan-out, and whatever the governance console reads. Build the shared sink layer for the engines; build the consumer for the effects.

---

## 8. What ADR-0004's "transport rebuild" blocker means now, concretely

The blocker as written (ADR-0004:42,64 — quoted in §1/P4) named one thing: the *inbound* consumer transport — `consumer.py`/`constants.py` off placeholder stream names and onto the `EventBus` port. **That work has substantially landed** since the ADR was accepted: commit `113037ce` rebuilt the consumer per ADR-0005 (`consumer.py:1-27` describes exactly the rebuild the ADR demanded; `constants.py:92-98` records `SUBSCRIBED_STREAMS`'s deletion; the worker entrypoint exists, `worker.py:139-194`).

So the transport-rebuild blocker today is **neither "the missing decision consumer" nor "still the inbound consumer" — it has migrated to the outbound side**, where it survives as the wire-parity blocker (ADR-0004:44): the OPA engine's two raw XADD paths (outbox relay, governance escalation) emit a format the live bus cannot decode (§4 W1/W2), so "what downstream consumers … receive" from the two engines is not merely non-parallel, it is disjoint — one decodes, one DLQs. The missing `evt:{tenant}:decision` consumer is **not in ADR-0004's blocker list at all**: line 44 presupposes "downstream consumers (audit, HITL, capital allocation)" exist to be protected. They do not exist. That gap is unrecorded in any accepted ADR — this audit is, at present, its only durable record.

Of the ADR's other two blockers: real Rego + live OPA server remains open (placeholder policy per `worker.py:33-37`); `policy_inputs.md` exists only as an **untracked** file with a copy-suffix name (`docs/04_decision_engine/policy_inputs (1).md`, present in `git status`, never committed) — in-repo, the blocker is formally unmet. **UNVERIFIED:** whether that untracked file's content satisfies the ADR's input-contract requirement; content review was out of scope.

---

## 9. Proposed terminal decomposition

Six terminals, ordered by dependency; file ownership is disjoint per terminal except where flagged. Not estimates — actual work units.

| # | Terminal | Owns (writes) | Depends on |
|---|----------|---------------|------------|
| T-A | **Neutral outcome record + publisher refactor.** Define the engine-neutral outcome type; rewrite `publish_outcome` to take it; delete `_extract_*`; add `decisions.source_entry_id` column (migration) and the monotonic guard on the terminal write. Delete dead `mirror_audit_step` or wire it — decide, don't leave it. | `decision_engine/models.py`, `decision_engine/publisher.py`, new migration, `tests/decision_engine/test_publisher.py` | — |
| T-B | **Staleness-key transport.** Widen the router handler contract to expose `DeliveredEvent`; thread `message_id` into `DecisionContext` (OPA) and `DecisionProposal` (inline). No behavior change beyond the new field. | `events/router.py`, `events/bus.py` (docstring only), `decision_engine/consumer.py`, `app/decision_engine/engine.py:113-128,143-159`, `app/decision_engine/events.py` | — |
| T-C | **Wire-format unification.** OutboxPoller relays the stored event JSON as the `{event: …}` envelope; `_emit_governance_event` goes through the bus port (or emits the envelope). The outbox `payload` column already holds the validated event (`publisher.py:309`) — this is relay-shape only. | `decision_engine/outbox_poller.py`, `decision_engine/hitl_writer.py:195-227`, their tests | — |
| T-D | **Identity unification.** One derivation for `decision_id`/`hitl_id`; both engines import it from one module. Owner must decide which namespace wins (recommend the OPA one — it is the side with durable rows). | `app/decision_engine/events.py:33-56`, `decision_engine/pipeline.py:63-83` (one of the two becomes a re-export), affected tests | — |
| T-E | **Inline engine onto the shared sinks.** Inline calls the T-A publisher (gaining a `decisions` row + the guard) and the HITL writer (gaining durable deferral tickets). This is the convergence commit; it touches both engines' composition. | `app/decision_engine/engine.py:162-281`, `bootstrap.py:259-276`, integration tests | T-A, T-B, T-D |
| T-F | **The decision consumer (downstream effects).** New consumer subscribing `decision` per tenant; sinks: capital commitment (new — the first real writer of `budget_ledger.committed`), projections/notifications. Write-before-ack, idempotent on `event_id`, guarded on `source_entry_id` — which requires the `schema_version` bump adding `source_entry_id` (and spend fields, for capital) to terminal `decision.*` payloads. | new `decision_consumer/` module (or a package under `app/`), `schemas/events/decision.py` (version bump), new migration for capital commitment, tests | T-B, T-C (cannot decode OPA events without it); payload bump coordinates with T-A |

T-A/T-B/T-C/T-D are mutually independent and can run in parallel; T-E joins three of them; T-F is last. The one deliberate file overlap: `schemas/events/decision.py` is touched only by T-F.

---

## 10. What I could not verify

- **Runtime behavior against live Postgres/Redis/OPA.** Everything here is static analysis plus the unit/integration suite (which passed 1104/1104 with 38 skips; skips not itemized). No live-infra test of the OutboxPoller ↔ RedisEventBus format mismatch was run — the claim rests on reading both codecs (`outbox_poller.py:131`, `redis_adapter.py:199-207`). **UNVERIFIED at runtime, high confidence from code.**
- **Migration files.** Column lists cited from the DAL SQL, not from reading migrations 0001/0009/0011 directly (e.g. the `hitl_queue` CHECK vocabulary is cited via `resume.py:27-29`'s docstring reference — a docstring, flagged accordingly). **UNVERIFIED against migration source.**
- **The 38 skipped tests** — whether any would exercise the paths above if unskipped. Not itemized.
- **Content of `policy_inputs (1).md`** (untracked): existence verified, adequacy not reviewed.
- **External artifacts** ADR-0004 cites (T15/T16/T18, the Word-doc ADR register): not in the repository (the ADR itself says so, ADR-0004:20); nothing here relies on them.
- **Consumers outside `src/skylize/**`** (n8n flows, external services reading Redis directly): out of the audited scope. The zero-consumer claim is scoped to this repository's source tree, exactly as the brief framed it.

---

*If these findings warrant a durable decision record, the candidates are: (1) the sink-convergence + neutral-outcome-record design (§7), and (2) the "decision consumer is a downstream-effects consumer, engines keep their own sinks" boundary (§5/§7) — the second contradicts an assumption ADR-0004 line 44 makes in passing, so it should be an explicit decision. Numbering is the owner's; no ADR is written by this audit.*
