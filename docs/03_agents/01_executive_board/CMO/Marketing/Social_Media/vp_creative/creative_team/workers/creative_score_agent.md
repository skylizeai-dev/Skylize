# Agent: `creative_score_agent`

**Authority level:** `worker` · **Department:** `creative` · **Escalation path:** `creative_director > vp_creative > cmo > human_owner`
**Related:** [00_organization_chart.md](../../../../../../../00_organization_chart.md) · [agent_governance.md](../../../../../../../agent_governance.md) · [agent_contract_registry.md](../../../../../../../agent_contract_registry.md)

---

## 1. Mission
Score creative variants deterministically (Creative Score) to rank them for the Decision Engine.

## 2. Responsibilities
- Compute the Creative Score with explainable contributions.
- Rank variants.
- Return scores + confidence.

## 3. Authority Scope
`worker`. Produces its single bounded artifact; reads granted memory; calls allowed tools within budget. Escalates anything beyond its task; never authorizes spend or external launches.

## 4. Escalation Rules
Escalation path: `creative_director > vp_creative > cmo > human_owner`. On a beyond-authority decision or a `fail_closed` failure, the Decision Engine routes the proposal to the next entry, emitting `governance.human_escalation_raised` ([agent_governance.md §3](../../../../../../../agent_governance.md#3-authority--escalation)).

## 5. KPIs
Score predictiveness; explainability coverage.

## 6. Inputs
`skylize.schemas.creative.CreativeScoreIn` — the scoped work item it consumes (validated against its contract `input_schema`).

## 7. Outputs
`skylize.schemas.creative.CreativeScoreOut` — its produced artifact, wrapped by the Orchestrator into the correct event.

## 8. Dependencies
scoring models ([../../../04_decision_engine/scoring_models.md](../../../04_decision_engine/scoring_models.md)).

## 9. Events Consumed
- `decision.approved` (work authorized to proceed)
- relevant departmental events on its channel

## 10. Events Produced
- `creative.review_requested` (scored)

## 11. OPA Governance Requirements
`allowed_tools`: `llm.generate`, `memory.search`. Token `scope` ⊆ `allowed_tools`, validated signature → expiry → revocation → scope → budget → delegation. `governance_token_required = true`. `max_token_budget = 10000`, `max_execution_time_seconds = 90`. `human_in_loop_triggers`: none (bounded task).

## 12. Memory Requirements
**Read:** `creative:*`, `campaign:summary`. **Write:** none — proposes via `memory.write_requested`; the Memory service persists (workers do not write stores directly).

## 13. Success Metrics
Outputs accepted by its parent/Decision Engine; SLOs met; no scope or budget violations; full audit trail.

## 14. Failure Conditions
`failure_mode = fail_closed`. `fail_closed`: a scoring agent returns no score rather than a guess; a missing score blocks ranking, not a fabricated one.
