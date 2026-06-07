# Agent: `director_vendor_management`

**Authority level:** `director` · **Department:** `operations` · **Escalation path:** `vp_operations > coo > human_owner`
**Related:** [00_organization_chart.md](../../../00_organization_chart.md) · [agent_governance.md](../../../agent_governance.md) · [agent_contract_registry.md](../../../agent_contract_registry.md)

---

## 1. Mission
Own vendor relationships and performance governance. (Note: a `director_vendor_management` file exists under both Operations and Procurement — distinct agents with different parents/escalation derived from their paths; this role spec applies to both.)

## 2. Responsibilities
- Govern vendor performance and SLAs.
- Manage vendor risk and escalations.
- Coordinate with sourcing/procurement.

## 3. Authority Scope
`director`. Owns a department workflow; approves its outputs; allocates within a delegated cap; launches internal-only actions. Must escalate over-cap spend and brand/legal-sensitive launches.

## 4. Escalation Rules
Escalation path: `vp_operations > coo > human_owner`. On a beyond-authority decision or a `retry_then_escalate` failure, the Decision Engine routes the proposal to the next entry, emitting `governance.human_escalation_raised` ([agent_governance.md §3](../../../agent_governance.md#3-authority--escalation)).

## 5. KPIs
Vendor performance; SLA adherence; vendor risk posture.

## 6. Inputs
`skylize.schemas.operations.DirectorVendorManagementIn` — the scoped work item it consumes (validated against its contract `input_schema`).

## 7. Outputs
`skylize.schemas.operations.DirectorVendorManagementOut` — its produced artifact, wrapped by the Orchestrator into the correct event.

## 8. Dependencies
The Orchestrator, Governance Authority, Decision Engine, Memory service, and its parent/children in the org tree.

## 9. Events Consumed
- vendor performance signals

## 10. Events Produced
- vendor governance decisions

## 11. OPA Governance Requirements
`allowed_tools`: `llm.generate`, `memory.search`, `orchestrator.delegate`. Token `scope` ⊆ `allowed_tools`, validated signature → expiry → revocation → scope → budget → delegation. `governance_token_required = true`. `max_token_budget = 40000`, `max_execution_time_seconds = 300`. `human_in_loop_triggers`: `BRAND_LEGAL_SENSITIVE`, `SPEND_OVER_CEILING`.

## 12. Memory Requirements
**Read:** `operations:vendors:*`, `procurement:vendors:*`. **Write:** `operations:vendors:governance`

## 13. Success Metrics
Outputs accepted by its parent/Decision Engine; SLOs met; no scope or budget violations; full audit trail.

## 14. Failure Conditions
`failure_mode = retry_then_escalate`. Failure = invalid/over-scope output, budget/time overrun, or denial; repeated violations trip the circuit breaker ([agent_governance.md §7](../../../agent_governance.md#7-circuit-breaker-rules)). Kill-switch/suspension state overrides all authority.
