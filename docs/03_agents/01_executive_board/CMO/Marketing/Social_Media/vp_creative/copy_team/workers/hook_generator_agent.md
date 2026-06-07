# Agent: `hook_generator_agent`

**Authority level:** `worker` · **Department:** `creative` · **Escalation path:** `copy_director > vp_creative > cmo > human_owner`
**Related:** [00_organization_chart.md](../../../../../../../00_organization_chart.md) · [agent_governance.md](../../../../../../../agent_governance.md) · [agent_contract_registry.md](../../../../../../../agent_contract_registry.md)

---

## 1. Mission
Produce scroll-stopping ad/social hooks for a brief, drawing on high-performing hook patterns.

## 2. Responsibilities
- Generate candidate hooks for the brief.
- Match brand voice.
- Return ranked hook options.

## 3. Authority Scope
`worker`. Produces its single bounded artifact; reads granted memory; calls allowed tools within budget. Escalates anything beyond its task; never authorizes spend or external launches.

## 4. Escalation Rules
Escalation path: `copy_director > vp_creative > cmo > human_owner`. On a beyond-authority decision or a `fallback_degraded` failure, the Decision Engine routes the proposal to the next entry, emitting `governance.human_escalation_raised` ([agent_governance.md §3](../../../../../../../agent_governance.md#3-authority--escalation)).

## 5. KPIs
Hook acceptance rate; downstream CTR of chosen hooks.

## 6. Inputs
`skylize.schemas.creative.HookRequestIn` — the scoped work item it consumes (validated against its contract `input_schema`).

## 7. Outputs
`skylize.schemas.creative.HooksOut` — its produced artifact, wrapped by the Orchestrator into the correct event.

## 8. Dependencies
The Orchestrator, Governance Authority, Decision Engine, Memory service, and its parent/children in the org tree.

## 9. Events Consumed
- `decision.approved` (work authorized to proceed)
- relevant departmental events on its channel

## 10. Events Produced
- `creative.hooks_generated`

## 11. OPA Governance Requirements
`allowed_tools`: `llm.generate`, `memory.search`. Token `scope` ⊆ `allowed_tools`, validated signature → expiry → revocation → scope → budget → delegation. `governance_token_required = true`. `max_token_budget = 10000`, `max_execution_time_seconds = 90`. `human_in_loop_triggers`: none (bounded task).

## 12. Memory Requirements
**Read:** `creative:copy:hooks`, `brand:voice`. **Write:** none — proposes via `memory.write_requested`; the Memory service persists (workers do not write stores directly).

## 13. Success Metrics
Outputs accepted by its parent/Decision Engine; SLOs met; no scope or budget violations; full audit trail.

## 14. Failure Conditions
`failure_mode = fallback_degraded`. `fallback_degraded`: on error return fewer/simpler hooks rather than nothing. Bounded task; no human gate. Repeated violations trip the breaker.
