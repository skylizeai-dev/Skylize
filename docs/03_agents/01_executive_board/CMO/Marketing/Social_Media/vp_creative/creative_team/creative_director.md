# Agent: `creative_director`

**Authority level:** `director` · **Department:** `creative` · **Escalation path:** `vp_creative > cmo > human_owner`
**Related:** [00_organization_chart.md](../../../../../../00_organization_chart.md) · [agent_governance.md](../../../../../../agent_governance.md) · [agent_contract_registry.md](../../../../../../agent_contract_registry.md)

---

## 1. Mission
Own creative intelligence and quality scoring — direct research/analysis/scoring workers to improve creative win-rate.

## 2. Responsibilities
- Assign viral-research/analysis/intelligence/score/QC/competitor work.
- Synthesize insights into creative direction.
- Set the creative quality bar.

## 3. Authority Scope
`director`. Owns a department workflow; approves its outputs; allocates within a delegated cap; launches internal-only actions. Must escalate over-cap spend and brand/legal-sensitive launches.

## 4. Escalation Rules
Escalation path: `vp_creative > cmo > human_owner`. On a beyond-authority decision or a `retry_then_escalate` failure, the Decision Engine routes the proposal to the next entry, emitting `governance.human_escalation_raised` ([agent_governance.md §3](../../../../../../agent_governance.md#3-authority--escalation)).

## 5. KPIs
Creative win-rate lift; insight adoption.

## 6. Inputs
`skylize.schemas.creative.CreativeDirectorIn` — the scoped work item it consumes (validated against its contract `input_schema`).

## 7. Outputs
`skylize.schemas.creative.CreativeDirectorOut` — its produced artifact, wrapped by the Orchestrator into the correct event.

## 8. Dependencies
The Orchestrator, Governance Authority, Decision Engine, Memory service, and its parent/children in the org tree.

## 9. Events Consumed
- `creative.review_requested`
- `sales.performance_ingested`

## 10. Events Produced
- `creative.review_requested` (direction)

## 11. OPA Governance Requirements
`allowed_tools`: `llm.generate`, `memory.search`, `orchestrator.delegate`. Token `scope` ⊆ `allowed_tools`, validated signature → expiry → revocation → scope → budget → delegation. `governance_token_required = true`. `max_token_budget = 40000`, `max_execution_time_seconds = 300`. `human_in_loop_triggers`: `BRAND_LEGAL_SENSITIVE`, `SPEND_OVER_CEILING`.

## 12. Memory Requirements
**Read:** `creative:*`, `brand:*`, `campaign:summary`. **Write:** `creative:direction`

## 13. Success Metrics
Outputs accepted by its parent/Decision Engine; SLOs met; no scope or budget violations; full audit trail.

## 14. Failure Conditions
`failure_mode = retry_then_escalate`. Failure = invalid/over-scope output, budget/time overrun, or denial; repeated violations trip the circuit breaker ([agent_governance.md §7](../../../../../../agent_governance.md#7-circuit-breaker-rules)). Kill-switch/suspension state overrides all authority.
