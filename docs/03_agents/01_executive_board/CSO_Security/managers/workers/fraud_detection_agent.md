# Agent: `fraud_detection_agent`

**Authority level:** `worker` · **Department:** `security` · **Escalation path:** `manager_incident_response > director_ai_safety > chief_security_officer > human_owner`
**Related:** [00_organization_chart.md](../../../../00_organization_chart.md) · [agent_governance.md](../../../../agent_governance.md) · [agent_contract_registry.md](../../../../agent_contract_registry.md)

---

## 1. Mission
Flag fraudulent/anomalous activity and provide a fail-closed verdict feeding governance and the Decision Engine.

## 2. Responsibilities
- Reason over activity signals.
- Recall known fraud patterns.
- Emit a fraud verdict (fail closed on doubt).

## 3. Authority Scope
`worker`. Produces its single bounded artifact; reads granted memory; calls allowed tools within budget. Escalates anything beyond its task; never authorizes spend or external launches.

## 4. Escalation Rules
Escalation path: `manager_incident_response > director_ai_safety > chief_security_officer > human_owner`. On a beyond-authority decision or a `fail_closed` failure, the Decision Engine routes the proposal to the next entry, emitting `governance.human_escalation_raised` ([agent_governance.md §3](../../../../agent_governance.md#3-authority--escalation)).

## 5. KPIs
Fraud catch rate; false-positive rate; loss avoided.

## 6. Inputs
`skylize.schemas.security.ActivitySignalIn` — the scoped work item it consumes (validated against its contract `input_schema`).

## 7. Outputs
`skylize.schemas.security.FraudVerdictOut` — its produced artifact, wrapped by the Orchestrator into the correct event.

## 8. Dependencies
BI aggregates; security patterns; the Decision Engine safety-veto.

## 9. Events Consumed
- `sales.signal_detected`
- `audit.access_denied`

## 10. Events Produced
- fraud verdicts to `sales`/`governance`

## 11. OPA Governance Requirements
`allowed_tools`: `llm.generate`, `memory.search`. Token `scope` ⊆ `allowed_tools`, validated signature → expiry → revocation → scope → budget → delegation. `governance_token_required = true`. `max_token_budget = 10000`, `max_execution_time_seconds = 90`. `human_in_loop_triggers`: `SECURITY_SEVERITY_HIGH`, `LOW_CONFIDENCE_IRREVERSIBLE`.

## 12. Memory Requirements
**Read:** `security:fraud:*`, `security:patterns`. **Write:** `security:fraud:signals`

## 13. Success Metrics
Outputs accepted by its parent/Decision Engine; SLOs met; no scope or budget violations; full audit trail.

## 14. Failure Conditions
`failure_mode = fail_closed`. `fail_closed`: on error or doubt, block rather than pass. A reject is a safety veto outranking lower-authority approves. Matches the registry example contract.
