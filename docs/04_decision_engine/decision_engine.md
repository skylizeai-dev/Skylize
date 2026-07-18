# Decision Engine

**Status:** Subsystem specification (source of truth for decision-making)
**Owner:** Principal Architect · `cto`
**Related:** [decision_flow.md](./decision_flow.md) · [scoring_models.md](./scoring_models.md) · [guardrails.md](./guardrails.md) · [capital_allocation.md](./capital_allocation.md) · [kill_switch_protocol.md](./kill_switch_protocol.md) · [../02_architecture/event_driven_architecture.md §7](../02_architecture/event_driven_architecture.md#7-the-decision-engine) · [../03_agents/agent_governance.md](../03_agents/agent_governance.md)

---

## 1. Purpose

The Decision Engine is the component that turns agent **intent** into authorized
**outcomes**. Agents reason and propose; the Decision Engine decides. Per
environment, exactly one Decision Engine implementation — selected by
`SKYLIZE_DECISION_ENGINE` — is the only component permitted to emit a terminal
`DecisionEvent` (`decision.approved` / `decision.rejected` /
`decision.deferred_to_human`), and therefore the single point where the
platform's authority model, policy guardrails, scoring, capital limits, and
human-in-the-loop rules are applied to real actions. See
[ADR-0004](../architecture/adr/0004-opa-production-arbiter.md) for the
per-environment selection between the OPA/Rego engine (`src/skylize/decision_engine/`,
production) and the inline evaluator (`src/skylize/app/decision_engine/`,
development/fallback).

It exists so that autonomy is **safe and accountable**: no spend, no external
launch, no irreversible action happens without passing one explainable,
audited, replayable evaluation.

## 2. Architectural role

The Decision Engine lives at the **Application Boundary**
([../02_architecture/system_boundaries.md §4.2](../02_architecture/system_boundaries.md#42-application-boundary--interfaces-if-agent-if-data-if-event)).
It is an event consumer/producer on the bus
([../02_architecture/event_driven_architecture.md §7](../02_architecture/event_driven_architecture.md#7-the-decision-engine)),
reads governance state from the Governance Authority, evaluates policy via OPA
([guardrails.md](./guardrails.md)), and applies scoring and capital rules
([scoring_models.md](./scoring_models.md), [capital_allocation.md](./capital_allocation.md)).
It never holds credentials and never reaches external systems — when it approves
an external action, an integration adapter executes it.

```
proposal events (creative.review_requested, sales.*_proposed, decision.conflict_detected)
        │  consumed via cg:decision_engine
        ▼
┌─────────────────── Decision Engine ───────────────────┐
│ 1 authority check (agent_level vs required_level)      │
│ 2 OPA policy evaluation (guardrails)                   │
│ 3 scoring (if ranking/sizing needed)                   │
│ 4 capital check (budget ceilings)                      │
│ 5 conflict resolution (if overlapping mandates)        │
│ 6 HITL gate (human_in_loop_triggers)                   │
└───────────────────────┬────────────────────────────────┘
        ▼                ▼                    ▼
decision.approved   decision.rejected   decision.deferred_to_human
        │ + mirrored AuditEvent for every step; one terminal outcome each
        ▼
adapter executes (approved) / nothing (rejected) / LangGraph pauses (deferred)
```

## 3. Inputs and outputs

**Consumes** (`cg:decision_engine`):
- `creative.review_requested`
- all `sales.*` proposals (`sales.campaign_proposed`, `sales.budget_reallocation_proposed`, …)
- `decision.conflict_detected`
- relevant `governance.*` (suspension, kill switch, token revocation)

**Emits:** `decision.evaluated` (the evaluation record), then **exactly one**
terminal outcome per proposal: `decision.approved`, `decision.rejected`, or
`decision.deferred_to_human`; plus `decision.conflict_detected` /
`decision.conflict_resolved` when mandates collide. Every step mirrors an
`AuditEvent`.

## 4. The six evaluation stages

Each inbound proposal passes the stages in order; the first stage that produces a
terminal outcome short-circuits the rest (most restrictive wins).

1. **Authority check** — compare the originating agent's `authority_level`
   against the action's required level. Exceeds authority → `deferred_to_human`
   along `escalation_path` ([agent_governance.md §3](../03_agents/agent_governance.md#3-authority--escalation)).
2. **Policy (OPA)** — evaluate the registered Rego policy for the action class.
   Deny → `rejected` with the violated rule named ([guardrails.md](./guardrails.md)).
3. **Scoring** — when the decision needs ranking or sizing (which creative,
   how much budget), compute the deterministic score ([scoring_models.md](./scoring_models.md)).
4. **Capital check** — verify spend against tenant/department/campaign ceilings
   ([capital_allocation.md](./capital_allocation.md)). Over ceiling →
   `deferred_to_human` (HITL `SPEND_OVER_CEILING`).
5. **Conflict resolution** — if a competing proposal exists on the same
   `partition_key`, resolve deterministically ([agent_governance.md §11](../03_agents/agent_governance.md#11-conflict-resolution)).
6. **HITL gate** — if any `human_in_loop_triggers` match, `deferred_to_human`.

Detailed branch logic, retries, and ACK semantics:
[decision_flow.md](./decision_flow.md).

## 5. Determinism and explainability

- The engine's decision logic is **deterministic given inputs**: the same
  proposal + the same policy version + the same scoring config + the same capital
  state always yields the same outcome. LLM reasoning happens *inside the
  proposing agent*, not inside the decision; the decision is auditable rule
  evaluation.
- Every outcome records: the rule(s) applied, the policy version, the scores, the
  capital snapshot, the `governance_token_id`, and the `correlation_id` — so any
  decision is fully reconstructable via replay
  ([../02_architecture/event_driven_architecture.md §10](../02_architecture/event_driven_architecture.md#10-event-replay-debugging--compliance)).

## 6. Failure handling

- Unhandled exception during evaluation → no ACK → redelivered → DLQ after the
  department's `dlq_after_retries`
  ([../02_architecture/event_driven_architecture.md §9](../02_architecture/event_driven_architecture.md#9-dead-letter-queue-strategy)).
- A proposal whose policy version is unknown is **never guessed**: it routes to
  the DLQ and raises `governance.human_escalation_raised`.
- Kill-switch state for the scope overrides every other stage: in scope →
  immediate `rejected`/quarantine, regardless of authority
  ([kill_switch_protocol.md](./kill_switch_protocol.md)).

## 7. Ownership & evolution

- **Owner:** Principal Architect for the engine contract; `director_platform`
  (CTO/Engineering) for the service; policy ownership is distributed (see
  [guardrails.md §7](./guardrails.md#7-ownership--evolution)).
- **Evolution:** new action classes register a policy + (optional) scoring model;
  the engine code does not change per class — it dispatches on action class to a
  registered policy. At Scale, the engine runs as a horizontally-scaled consumer
  group; per-`partition_key` pinning preserves ordering
  ([../architecture/03_agent_runtime.md §9](../architecture/03_agent_runtime.md#9-concurrency--scaling)).
