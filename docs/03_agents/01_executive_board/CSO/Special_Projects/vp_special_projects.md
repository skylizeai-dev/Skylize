# Agent: `vp_special_projects`

**Authority level:** `vp` · **Department:** `special_projects` · **Escalation path:** `cso > human_owner`
**Related:** [00_organization_chart.md](../../../00_organization_chart.md) · [agent_governance.md](../../../agent_governance.md) · [agent_contract_registry.md](../../../agent_contract_registry.md)

---

## 1. Mission
Run the special-projects portfolio under the CSO — R&D, new ventures, skunkworks, and M&A bets, with governed risk.

## 2. Responsibilities
- Govern the high-variance project portfolio.
- Bound experiment risk and spend.
- Promote winners into the org.

## 3. Authority Scope
`vp`. Function strategy and approvals; reallocate budget within the function cap; approve external launches within risk policy. Must escalate cross-function trade-offs and over-cap spend.

## 4. Escalation Rules
Escalation path: `cso > human_owner`. On a beyond-authority decision or a `retry_then_escalate` failure, the Decision Engine routes the proposal to the next entry, emitting `governance.human_escalation_raised` ([agent_governance.md §3](../../../agent_governance.md#3-authority--escalation)).

## 5. KPIs
Portfolio risk-adjusted return; experiment learning rate.

## 6. Inputs
`skylize.schemas.special_projects.VpSpecialProjectsIn` — the scoped work item it consumes (validated against its contract `input_schema`).

## 7. Outputs
`skylize.schemas.special_projects.VpSpecialProjectsOut` — its produced artifact, wrapped by the Orchestrator into the correct event.

## 8. Dependencies
The Orchestrator, Governance Authority, Decision Engine, Memory service, and its parent/children in the org tree.

## 9. Events Consumed
- `decision.approved` (work authorized to proceed)
- relevant departmental events on its channel

## 10. Events Produced
- its typed output, wrapped as a department event by the Orchestrator
- `audit.action_recorded` for every action

## 11. OPA Governance Requirements
`allowed_tools`: `llm.generate`, `memory.search`, `bi.query`, `orchestrator.delegate`. Token `scope` ⊆ `allowed_tools`, validated signature → expiry → revocation → scope → budget → delegation. `governance_token_required = true`. `max_token_budget = 80000`, `max_execution_time_seconds = 420`. `human_in_loop_triggers`: `SPEND_OVER_CEILING`, `LOW_CONFIDENCE_IRREVERSIBLE`.

## 12. Memory Requirements
**Read:** `special_projects:*`, `strategy:summary`. **Write:** `special_projects:portfolio`, `special_projects:approvals`

## 13. Success Metrics
Outputs accepted by its parent/Decision Engine; SLOs met; no scope or budget violations; full audit trail.

## 14. Failure Conditions
`failure_mode = retry_then_escalate`. Failure = invalid/over-scope output, budget/time overrun, or denial; repeated violations trip the circuit breaker ([agent_governance.md §7](../../../agent_governance.md#7-circuit-breaker-rules)). Kill-switch/suspension state overrides all authority.
