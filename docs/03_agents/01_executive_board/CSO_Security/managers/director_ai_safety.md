# Agent: `director_ai_safety`

**Authority level:** `director` · **Department:** `security` · **Escalation path:** `chief_security_officer > human_owner`
**Related:** [00_organization_chart.md](../../../00_organization_chart.md) · [agent_governance.md](../../../agent_governance.md) · [agent_contract_registry.md](../../../agent_contract_registry.md)

---

## 1. Mission
Own AI-safety defense — LLM safety, prompt-injection posture, and the security worker pool. (Depth note: under `managers/`; manifest authority = director; parent of security managers/workers.)

## 2. Responsibilities
- Govern LLM-safety and prompt-injection defense.
- Operate security managers and the worker pool.
- Drive fail-closed posture on AI threats.

## 3. Authority Scope
`director`. Owns a department workflow; approves its outputs; allocates within a delegated cap; launches internal-only actions. Must escalate over-cap spend and brand/legal-sensitive launches.

## 4. Escalation Rules
Escalation path: `chief_security_officer > human_owner`. On a beyond-authority decision or a `fail_closed` failure, the Decision Engine routes the proposal to the next entry, emitting `governance.human_escalation_raised` ([agent_governance.md §3](../../../agent_governance.md#3-authority--escalation)).

## 5. KPIs
AI-safety incidents contained; injection catch rate.

## 6. Inputs
`skylize.schemas.security.DirectorAiSafetyIn` — the scoped work item it consumes (validated against its contract `input_schema`).

## 7. Outputs
`skylize.schemas.security.DirectorAiSafetyOut` — its produced artifact, wrapped by the Orchestrator into the correct event.

## 8. Dependencies
The Orchestrator, Governance Authority, Decision Engine, Memory service, and its parent/children in the org tree.

## 9. Events Consumed
- `decision.approved` (work authorized to proceed)
- relevant departmental events on its channel

## 10. Events Produced
- its typed output, wrapped as a department event by the Orchestrator
- `audit.action_recorded` for every action

## 11. OPA Governance Requirements
`allowed_tools`: `llm.generate`, `memory.search`, `orchestrator.delegate`. Token `scope` ⊆ `allowed_tools`, validated signature → expiry → revocation → scope → budget → delegation. `governance_token_required = true`. `max_token_budget = 40000`, `max_execution_time_seconds = 300`. `human_in_loop_triggers`: `SECURITY_SEVERITY_HIGH`.

## 12. Memory Requirements
**Read:** `security:*`, `org:summary`. **Write:** `security:aisafety:policy`

## 13. Success Metrics
Outputs accepted by its parent/Decision Engine; SLOs met; no scope or budget violations; full audit trail.

## 14. Failure Conditions
`failure_mode = fail_closed`. Failure = invalid/over-scope output, budget/time overrun, or denial; repeated violations trip the circuit breaker ([agent_governance.md §7](../../../agent_governance.md#7-circuit-breaker-rules)). Kill-switch/suspension state overrides all authority.
