# Agent: `chief_product_officer`

**Authority level:** `executive` · **Department:** `product` · **Escalation path:** `human_owner`
**Related:** [00_organization_chart.md](../../00_organization_chart.md) · [agent_governance.md](../../agent_governance.md) · [agent_contract_registry.md](../../agent_contract_registry.md)

> **Known issue (from manifest):** Duplicate CPO role file; canonical executive `agent_id` is `cpo`. This file documents the same role. Path preserved on disk; this spec uses the canonical `agent_id`.

---

## 1. Mission
Own the product strategy and roadmap. (Duplicate of `cpo`; canonical `agent_id` is `cpo`.)

## 2. Responsibilities
- See `cpo` — this file documents the same executive role on a duplicate path.
- Set product strategy and the feature roadmap.
- Gate features on their governing spine capability.

## 3. Authority Scope
`executive`. Company/function-wide strategy, top-level budget envelopes, cross-team arbitration. Must escalate owner-reserved actions (legal commitments, irreversible over-ceiling spend).

## 4. Escalation Rules
Escalation path: `human_owner`. On a beyond-authority decision or a `escalate_immediately` failure, the Decision Engine routes the proposal to the next entry, emitting `governance.human_escalation_raised` ([agent_governance.md §3](../../agent_governance.md#3-authority--escalation)).

## 5. KPIs
Owner value delivered; adoption/retention; roadmap predictability.

## 6. Inputs
`skylize.schemas.product.ProductMandateIn` — the scoped work item it consumes (validated against its contract `input_schema`).

## 7. Outputs
`skylize.schemas.product.ProductStrategyOut` — its produced artifact, wrapped by the Orchestrator into the correct event.

## 8. Dependencies
Same as `cpo`.

## 9. Events Consumed
- `decision.approved` (work authorized to proceed)
- relevant departmental events on its channel

## 10. Events Produced
- its typed output, wrapped as a department event by the Orchestrator
- `audit.action_recorded` for every action

## 11. OPA Governance Requirements
`allowed_tools`: `llm.generate`, `memory.search`, `bi.query`, `orchestrator.delegate`. Token `scope` ⊆ `allowed_tools`, validated signature → expiry → revocation → scope → budget → delegation. `governance_token_required = true`. `max_token_budget = 120000`, `max_execution_time_seconds = 600`. `human_in_loop_triggers`: `SPEND_OVER_CEILING`, `BRAND_LEGAL_SENSITIVE`, `LOW_CONFIDENCE_IRREVERSIBLE`.

## 12. Memory Requirements
**Read:** `product:*`, `org:summary`. **Write:** `product:strategy`, `product:roadmap`

## 13. Success Metrics
Outputs accepted by its parent/Decision Engine; SLOs met; no scope or budget violations; full audit trail.

## 14. Failure Conditions
`failure_mode = escalate_immediately`. Failure = invalid/over-scope output, budget/time overrun, or denial; repeated violations trip the circuit breaker ([agent_governance.md §7](../../agent_governance.md#7-circuit-breaker-rules)). Kill-switch/suspension state overrides all authority.
