# Agent: `workflow_manager_agent`

**Authority level:** `worker` · **Department:** `creative` · **Escalation path:** `creative_operations_manager > director_social_media > vp_creative > cmo > human_owner`
**Related:** [00_organization_chart.md](../../../../../../../00_organization_chart.md) · [agent_governance.md](../../../../../../../agent_governance.md) · [agent_contract_registry.md](../../../../../../../agent_contract_registry.md)

---

## 1. Mission
Orchestrate the creative production workflow stages end to end.

## 2. Responsibilities
- Advance items through workflow stages.
- Detect bottlenecks.
- Coordinate hand-offs.

## 3. Authority Scope
`worker`. Produces its single bounded artifact; reads granted memory; calls allowed tools within budget. Escalates anything beyond its task; never authorizes spend or external launches.

## 4. Escalation Rules
Escalation path: `creative_operations_manager > director_social_media > vp_creative > cmo > human_owner`. On a beyond-authority decision or a `retry_then_escalate` failure, the Decision Engine routes the proposal to the next entry, emitting `governance.human_escalation_raised` ([agent_governance.md §3](../../../../../../../agent_governance.md#3-authority--escalation)).

## 5. KPIs
Throughput; bottleneck resolution time.

## 6. Inputs
`skylize.schemas.creative.WorkflowManagerAgentIn` — the scoped work item it consumes (validated against its contract `input_schema`).

## 7. Outputs
`skylize.schemas.creative.WorkflowManagerAgentOut` — its produced artifact, wrapped by the Orchestrator into the correct event.

## 8. Dependencies
The Orchestrator, Governance Authority, Decision Engine, Memory service, and its parent/children in the org tree.

## 9. Events Consumed
- `decision.approved` (work authorized to proceed)
- relevant departmental events on its channel

## 10. Events Produced
- its typed output, wrapped as a department event by the Orchestrator
- `audit.action_recorded` for every action

## 11. OPA Governance Requirements
`allowed_tools`: `llm.generate`, `memory.search`. Token `scope` ⊆ `allowed_tools`, validated signature → expiry → revocation → scope → budget → delegation. `governance_token_required = true`. `max_token_budget = 10000`, `max_execution_time_seconds = 90`. `human_in_loop_triggers`: none (bounded task).

## 12. Memory Requirements
**Read:** `creative:ops:workflow`. **Write:** none — proposes via `memory.write_requested`; the Memory service persists (workers do not write stores directly).

## 13. Success Metrics
Outputs accepted by its parent/Decision Engine; SLOs met; no scope or budget violations; full audit trail.

## 14. Failure Conditions
`failure_mode = retry_then_escalate`. Failure = invalid/over-scope output, budget/time overrun, or denial; repeated violations trip the circuit breaker ([agent_governance.md §7](../../../../../../../agent_governance.md#7-circuit-breaker-rules)). Kill-switch/suspension state overrides all authority.
