# Decision Flow

**Status:** Subsystem specification (source of truth for evaluation flow)
**Owner:** Principal Architect · `director_platform`
**Related:** [decision_engine.md](./decision_engine.md) · [guardrails.md](./guardrails.md) · [scoring_models.md](./scoring_models.md) · [../02_architecture/event_driven_architecture.md §7-8](../02_architecture/event_driven_architecture.md#7-the-decision-engine) · [../03_agents/agent_governance.md §3,§9,§11](../03_agents/agent_governance.md#3-authority--escalation)

---

## 1. Purpose

This document specifies the **precise control flow** of a single decision: from a
proposal arriving on the bus to exactly one terminal outcome, including every
branch (autonomous approve, reject, escalate, conflict, HITL pause/resume) and the
delivery semantics (ACK, retry, DLQ, idempotency) that keep it exactly-once in
effect despite at-least-once delivery.

## 2. Architectural role

Where [decision_engine.md](./decision_engine.md) defines *what* the engine is,
this defines *how a decision executes step by step*. It is the runtime contract
the `decision-engine` service implements and the basis for the LangGraph
human-in-the-loop pause/resume behavior
([../architecture/03_agent_runtime.md §8](../architecture/03_agent_runtime.md#8-escalation-hitl-and-conflict-at-runtime)).

## 3. The flow

```
                ┌─────────────────────────────┐
 proposal evt ─▶│ read from cg:decision_engine │
                └──────────────┬──────────────┘
                               ▼
              idempotency: seen event_id before? ──yes──▶ ACK, drop (already decided)
                               │ no
                               ▼
            load governance state (kill switch / suspension / token validity)
                               │
        kill-switch in scope? ─┴─yes──▶ decision.rejected + quarantine ──▶ ACK
                               │ no
                               ▼
                emit decision.evaluated (open the record)
                               │
                 ┌─────────────▼─────────────┐
   STAGE 1       │ authority_level sufficient│──no──▶ decision.deferred_to_human
   authority     │  for required level?      │        + governance.human_escalation_raised
                 └─────────────┬─────────────┘        (route to next in escalation_path)
                               │ yes
                 ┌─────────────▼─────────────┐
   STAGE 2 OPA   │ policy allow?             │──deny─▶ decision.rejected (rule named)
                 └─────────────┬─────────────┘
                               │ allow
                 ┌─────────────▼─────────────┐
   STAGE 3       │ scoring (if needed)       │  (annotate record; never terminal alone)
                 └─────────────┬─────────────┘
                 ┌─────────────▼─────────────┐
   STAGE 4       │ within capital ceiling?   │──no──▶ decision.deferred_to_human
   capital       └─────────────┬─────────────┘        (HITL: SPEND_OVER_CEILING)
                               │ yes
                 ┌─────────────▼─────────────┐
   STAGE 5       │ competing proposal on same│──yes─▶ decision.conflict_detected
   conflict      │ partition_key?            │        → resolve → decision.conflict_resolved
                 └─────────────┬─────────────┘
                               │ no
                 ┌─────────────▼─────────────┐
   STAGE 6 HITL  │ any human_in_loop_trigger?│──yes─▶ decision.deferred_to_human (pause graph)
                 └─────────────┬─────────────┘
                               │ no
                               ▼
                        decision.approved
                               │
                               ▼  ACK + mirrored AuditEvent for every step above
```

## 4. Branch outcomes

| Outcome | Meaning | Next |
|---|---|---|
| `decision.approved` | action authorized | adapter executes (external) / DAL persists (internal) |
| `decision.rejected` | denied by policy/kill-switch/conflict-loss | nothing actionable; proposer's `failure_mode` applies |
| `decision.deferred_to_human` | needs human approval | LangGraph pauses at a durable HITL node; resumes on human verdict |

A proposal yields **exactly one** terminal outcome. The engine never silently
drops or double-decides.

## 5. Escalation routing

When STAGE 1 or STAGE 4 defers, the engine routes to the **next entry** in the
proposing agent's `escalation_path` (an ordered chain ending at `human_owner`,
e.g. `hook_generator_agent → copy_director → vp_creative → cmo → ceo →
human_owner`). It emits `governance.human_escalation_raised`. The recipient may
approve, modify, or reject; each is audited. The chain is realized exactly as in
[agent_governance.md §3](../03_agents/agent_governance.md#3-authority--escalation).

## 6. Conflict resolution (STAGE 5 detail)

Two proposals on the same `partition_key` (e.g. `campaign:42`) with incompatible
terminal intents trigger `decision.conflict_detected`, resolved by the
deterministic order ([agent_governance.md §11](../03_agents/agent_governance.md#11-conflict-resolution)):

1. **Authority precedence** (executive > vp > director > manager > worker)
2. **Safety veto** (security/brand/legal/compliance *reject* beats a peer/lower *approve*)
3. **Explicit policy** (registered conflict-class rule, e.g. "budget conflicts → lower spend pending review")
4. **Same level, no policy** → escalate to nearest common ancestor / HITL

The engine emits `decision.conflict_resolved` naming the winner and the rule
applied. Conflicts are never coin-flipped; the rule is always recorded.

## 7. HITL pause & resume (STAGE 6 detail)

- On a deferred decision, the LangGraph workflow checkpoints to Postgres and
  **pauses** at the HITL node — durable, so any worker can resume it.
- The human verdict (approve / modify / reject) arrives via the control plane
  (authenticated at `IF-EDGE`), and the graph **resumes** from the checkpoint.
- Each verdict is audited; a modify re-enters the flow at STAGE 2 with the new
  parameters. Timeouts route to a configurable fallback (default: keep paused +
  re-notify).

## 8. Delivery semantics

- **At-least-once** delivery (Redis Streams consumer group); the engine is
  **idempotent on `event_id`** — a redelivered proposal that was already decided
  is ACKed and dropped.
- **Ordering** is per `partition_key` (consistent-hash pinned to one consumer),
  so two proposals on the same campaign are evaluated in order
  ([../02_architecture/event_driven_architecture.md §8](../02_architecture/event_driven_architecture.md#8-ordering-guarantees)).
- **Retry/DLQ:** unhandled failure → no ACK → redelivered → DLQ after
  `dlq_after_retries`, with an `AuditEvent` and a `governance.*` signal if
  governance-relevant.

## 9. Ownership & evolution

- **Owner:** `director_platform` for the implementation; Principal Architect for
  the flow contract.
- **Evolution:** new branches (e.g. a "shadow decision" preview mode for replay)
  are added as additional LangGraph nodes; the terminal-outcome invariant (exactly
  one) is never relaxed. Stage order is fixed by policy; only stage *contents*
  (policies, scores, ceilings) are configurable per tenant within platform floors.
