# Agent: `prompt_injection_agent`

**Authority level:** `worker` · **Department:** `security` · **Escalation path:** `manager_incident_response > director_ai_safety > chief_security_officer > human_owner`
**Related:** [00_organization_chart.md](../../../../00_organization_chart.md) · [agent_governance.md](../../../../agent_governance.md) · [agent_contract_registry.md](../../../../agent_contract_registry.md)

---

## 1. Mission
Detect and block prompt-injection attempts against agents and via untrusted inputs (e.g. MCP/web content).

## 2. Responsibilities
- Screen untrusted inputs for injection.
- Block/flag injection attempts.
- Raise high-severity HITL.

## 3. Authority Scope
`worker`. Produces its single bounded artifact; reads granted memory; calls allowed tools within budget. Escalates anything beyond its task; never authorizes spend or external launches.

## 4. Escalation Rules
Escalation path: `manager_incident_response > director_ai_safety > chief_security_officer > human_owner`. On a beyond-authority decision or a `fail_closed` failure, the Decision Engine routes the proposal to the next entry, emitting `governance.human_escalation_raised` ([agent_governance.md §3](../../../../agent_governance.md#3-authority--escalation)).

## 5. KPIs
Injection catch rate; false-positive rate.

## 6. Inputs
`skylize.schemas.security.PromptInjectionAgentIn` — the scoped work item it consumes (validated against its contract `input_schema`).

## 7. Outputs
`skylize.schemas.security.PromptInjectionAgentOut` — its produced artifact, wrapped by the Orchestrator into the correct event.

## 8. Dependencies
The Orchestrator, Governance Authority, Decision Engine, Memory service, and its parent/children in the org tree.

## 9. Events Consumed
- `decision.approved` (work authorized to proceed)
- relevant departmental events on its channel

## 10. Events Produced
- injection verdicts

## 11. OPA Governance Requirements
`allowed_tools`: `llm.generate`. Token `scope` ⊆ `allowed_tools`, validated signature → expiry → revocation → scope → budget → delegation. `governance_token_required = true`. `max_token_budget = 10000`, `max_execution_time_seconds = 90`. `human_in_loop_triggers`: `SECURITY_SEVERITY_HIGH`.

## 12. Memory Requirements
`memory_read_access: []`, `memory_write_access: []`. All Safety Suite agents are stateless — they must evaluate each run in isolation, since cross-run state risks anchoring bias and audit contamination.

## 13. Success Metrics
Outputs accepted by its parent/Decision Engine; SLOs met; no scope or budget violations; full audit trail.

## 14. Failure Conditions
`failure_mode = fail_closed`. Failure = invalid/over-scope output, budget/time overrun, or denial; repeated violations trip the circuit breaker ([agent_governance.md §7](../../../../agent_governance.md#7-circuit-breaker-rules)). Kill-switch/suspension state overrides all authority.
