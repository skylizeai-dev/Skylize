# Agent: `manager_incident_response`

**Authority level:** `manager` · **Department:** `security` · **Escalation path:** `director_ai_safety > chief_security_officer > human_owner`
**Related:** [00_organization_chart.md](../../../00_organization_chart.md) · [agent_governance.md](../../../agent_governance.md) · [agent_contract_registry.md](../../../agent_contract_registry.md)

---

## 1. Mission
Run incident response operations — the incident commander for security incidents. (See [../../../08_operations/incident_response.md](../../../08_operations/incident_response.md).)

## 2. Responsibilities
- Triage and command incidents.
- Coordinate containment (breaker/kill switch readiness).
- Run postmortems.

## 3. Authority Scope
`manager`. Routes/QAs worker outputs; approves within a small pre-set threshold. Must escalate any spend, external publish, or cross-team coordination.

## 4. Escalation Rules
Escalation path: `director_ai_safety > chief_security_officer > human_owner`. On a beyond-authority decision or a `fail_closed` failure, the Decision Engine routes the proposal to the next entry, emitting `governance.human_escalation_raised` ([agent_governance.md §3](../../../agent_governance.md#3-authority--escalation)).

## 5. KPIs
Mean-time-to-stop; incident recurrence.

## 6. Inputs
`skylize.schemas.security.ManagerIncidentResponseIn` — the scoped work item it consumes (validated against its contract `input_schema`).

## 7. Outputs
`skylize.schemas.security.ManagerIncidentResponseOut` — its produced artifact, wrapped by the Orchestrator into the correct event.

## 8. Dependencies
The Orchestrator, Governance Authority, Decision Engine, Memory service, and its parent/children in the org tree.

## 9. Events Consumed
- `decision.approved` (work authorized to proceed)
- relevant departmental events on its channel

## 10. Events Produced
- its typed output, wrapped as a department event by the Orchestrator
- `audit.action_recorded` for every action

## 11. OPA Governance Requirements
`allowed_tools`: `llm.generate`, `memory.search`, `orchestrator.delegate`. Token `scope` ⊆ `allowed_tools`, validated signature → expiry → revocation → scope → budget → delegation. `governance_token_required = true`. `max_token_budget = 20000`, `max_execution_time_seconds = 180`. `human_in_loop_triggers`: `SECURITY_SEVERITY_HIGH`.

## 12. Memory Requirements
**Read:** `security:incident:*`. **Write:** `security:incident:records`, `org:lessons`

## 13. Success Metrics
Outputs accepted by its parent/Decision Engine; SLOs met; no scope or budget violations; full audit trail.

## 14. Failure Conditions
`failure_mode = fail_closed`. Failure = invalid/over-scope output, budget/time overrun, or denial; repeated violations trip the circuit breaker ([agent_governance.md §7](../../../agent_governance.md#7-circuit-breaker-rules)). Kill-switch/suspension state overrides all authority.
